import os
import io
import zipfile
from datetime import datetime, timedelta

import fitz
import networkx as nx
import streamlit as st
import streamlit.components.v1 as components
from langchain_community.vectorstores import FAISS
from pyvis.network import Network

from agent import LangChainAssistant
from db import (
    add_chat_message,
    add_qa_metric,
    add_quiz_attempt,
    add_reminder,
    create_shared_report,
    delete_pending_plan_reminders,
    get_chat_history,
    get_course,
    get_kg_graph,
    get_latest_course_outline,
    get_pending_reminders,
    get_quiz_attempts,
    get_qa_metrics,
    get_review_plan_items,
    get_shared_report,
    get_summaries,
    init_db,
    list_courses,
    mark_reminder_done,
    replace_review_plan_items,
    save_course_outline,
    update_review_plan_item_status,
    get_trusted_sources,
    replace_trusted_sources,
)
from paths import get_exports_dir, get_faiss_dir

init_db()

st.set_page_config(page_title="SkimClass - 复习中心", layout="wide", page_icon="📖")
st.title("📖 复习中心")

DEFAULT_PUBLIC_BASE_URL = os.getenv("SKIM_PUBLIC_BASE_URL", "")


def _build_share_link(token, base_url):
    clean = str(base_url or "").strip()
    if not clean:
        return f"?share_token={token}"
    return f"{clean.rstrip('/')}/?share_token={token}"


def _zip_report_bundle(course_name, report_md, summaries, plan_items, quiz_attempts):
    """构建可分享的学习资料压缩包。"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.md", report_md)
        zf.writestr("report.txt", report_md)

        notes_text = "\n\n".join([f"## 段落 {s['segment_index']}\n{s['content']}" for s in summaries])
        zf.writestr("notes.md", notes_text)

        plan_lines = ["weak_point,recommended_material,estimated_minutes,priority,due_date,status"]
        for p in plan_items:
            plan_lines.append(
                f"{p['weak_point']},{p['recommended_material']},{p['estimated_minutes']},{p['priority']},{p['due_date']},{p['status']}"
            )
        zf.writestr("review_plan.csv", "\n".join(plan_lines))

        trend_lines = ["attempt,total_questions,correct_questions,created_at"]
        for idx, a in enumerate(quiz_attempts, 1):
            trend_lines.append(
                f"{idx},{a.get('total_questions', 0)},{a.get('correct_questions', 0)},{a.get('created_at', '')}"
            )
        zf.writestr("quiz_trend.csv", "\n".join(trend_lines))

        zf.writestr("meta.txt", f"course={course_name}\nexport_time={datetime.now().isoformat()}\n")

    out.seek(0)
    return out.getvalue()


def _report_markdown_to_pdf_bytes(report_md, title="SkimClass Report"):
    """将 Markdown 文本转换为可下载 PDF。"""
    doc = fitz.open()
    page = doc.new_page()
    x = 50
    y = 60
    line_h = 18
    max_y = 780

    page.insert_text((x, y), title, fontsize=16)
    y += 28

    for raw in (report_md or "").splitlines():
        line = raw.replace("#", "").replace("*", "").strip()
        if not line:
            y += line_h
            continue

        wrapped = []
        chunk = ""
        for ch in line:
            chunk += ch
            if len(chunk) >= 45:
                wrapped.append(chunk)
                chunk = ""
        if chunk:
            wrapped.append(chunk)

        for seg in wrapped:
            if y > max_y:
                page = doc.new_page()
                y = 60
            page.insert_text((x, y), seg, fontsize=11)
            y += line_h

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


share_token = st.query_params.get("share_token")
if share_token:
    shared = get_shared_report(share_token)
    if not shared:
        st.error("分享链接无效或已被删除。")
        st.stop()

    expired = False
    try:
        expired = datetime.fromisoformat(shared["expires_at"]) < datetime.now()
    except Exception:
        pass

    if expired:
        st.warning("该分享链接已过期。")
        st.stop()

    course = get_course(shared["course_id"])
    course_name = course["name"] if course else "未知课程"
    st.info(f"分享课程：{course_name} | 过期时间：{shared['expires_at'].replace('T', ' ')[:16]}")
    st.markdown(shared["report_markdown"])
    st.download_button(
        "下载分享报告(Markdown)",
        data=shared["report_markdown"].encode("utf-8"),
        file_name=f"shared_report_{share_token}.md",
        mime="text/markdown",
    )
    st.stop()


def _answer_label(option_text, idx):
    text = (option_text or "").strip()
    if text and text[0] in "ABCD":
        return text[0]
    return "ABCD"[idx] if idx < 4 else "A"


def _retrieve_docs_with_scores(vector_store, query, k=4):
    if not vector_store:
        return []
    try:
        return vector_store.similarity_search_with_score(query, k=k)
    except Exception:
        docs = vector_store.similarity_search(query, k=k)
        return [(d, None) for d in docs]


def _filter_docs_by_source(pairs, allowed_sources, max_score=None):
    allowed = set(allowed_sources or [])
    kept = []
    for doc, score in pairs:
        src = str(doc.metadata.get("source", "textbook"))
        if allowed and src not in allowed:
            continue
        if max_score is not None and score is not None and score > max_score:
            continue
        kept.append((doc, score))
    return kept


def _build_weak_points(attempts):
    """从测验记录汇总薄弱点与错因。"""
    agg = {}
    for att in attempts:
        for item in att.get("wrong_details", []):
            kp = (item.get("knowledge_point") or "核心知识点").strip()
            if kp not in agg:
                agg[kp] = {
                    "weak_point": kp,
                    "wrong_count": 0,
                    "causes": {},
                }
            agg[kp]["wrong_count"] += 1
            cause = (item.get("wrong_cause") or "概念理解偏差").strip()
            agg[kp]["causes"][cause] = agg[kp]["causes"].get(cause, 0) + 1

    sorted_items = sorted(agg.values(), key=lambda x: x["wrong_count"], reverse=True)
    weak_points = []
    for item in sorted_items:
        top_cause = "概念理解偏差"
        if item["causes"]:
            top_cause = sorted(item["causes"].items(), key=lambda x: x[1], reverse=True)[0][0]
        weak_points.append(
            {
                "weak_point": item["weak_point"],
                "wrong_count": item["wrong_count"],
                "main_cause": top_cause,
            }
        )
    return weak_points


def _upsert_outline_if_needed(selected_course_id, summaries, assistant, can_call_llm):
    sig = f"{selected_course_id}:{len(summaries)}:{summaries[-1]['id'] if summaries else 0}"
    cache_key = f"outline_sig_{selected_course_id}"
    latest_outline = get_latest_course_outline(selected_course_id)

    if can_call_llm and summaries and st.session_state.get(cache_key) != sig:
        with st.spinner("正在自动合并相邻分段并生成课程大纲..."):
            outline = assistant.build_course_outline(summaries)
            save_course_outline(selected_course_id, outline)
            st.session_state[cache_key] = sig
            latest_outline = get_latest_course_outline(selected_course_id)
    return latest_outline


# ========== 侧边栏: API Key + 课程选择 ==========
with st.sidebar:
    api_key = st.text_input("🔑 API Key", type="password")

    st.divider()
    st.subheader("🤖 模型配置")
    summary_model = st.text_input("总结模型", value="qwen3-omni-flash")
    embedding_model = st.text_input("向量模型", value="text-embedding-v4")

    st.divider()
    courses = list_courses()
    if not courses:
        st.info("还没有任何课程记录，请先去主页录制课程。")
        st.stop()

    options = {f"{c['name']} ({c['created_at'][:16]})": c["id"] for c in courses}
    selected_label = st.selectbox("📚 选择课程", list(options.keys()))
    selected_course_id = options[selected_label]

    st.divider()
    st.subheader("🛡 可信来源白名单")
    trusted_domains = get_trusted_sources(selected_course_id)
    trusted_text = st.text_area("域名白名单(一行一个)", value="\n".join(trusted_domains), height=100)
    if st.button("保存白名单", use_container_width=True):
        domains = [x.strip().lower() for x in trusted_text.replace(",", "\n").splitlines() if x.strip()]
        replace_trusted_sources(selected_course_id, domains)
        trusted_domains = get_trusted_sources(selected_course_id)
        st.success("白名单已更新")

    st.divider()
    st.subheader("🧾 可信问答策略")
    allowed_sources = st.multiselect(
        "允许引用来源",
        ["textbook", "notes"],
        default=["textbook", "notes"],
    )
    strict_evidence_mode = st.checkbox("低置信度时拒答", value=True)
    max_retrieval_score = st.number_input("检索最大距离阈值", min_value=0.1, max_value=5.0, value=1.2, step=0.1)

assistant = LangChainAssistant(api_key, summary_model, embedding_model) if api_key else None

# ========== 主区域: 七个 Tab ==========
tab_notes, tab_quiz, tab_qa, tab_kg, tab_plan, tab_materials, tab_reminders, tab_metrics = st.tabs(
    ["📝 笔记回顾", "🧪 边学边测", "💬 历史问答", "🕸️ 知识图谱", "🧭 复习清单", "📚 材料推荐", "⏰ 主动提醒", "📈 评测指标"]
)

# ---- Tab 1: 笔记回顾 + 自动课程大纲 ----
with tab_notes:
    summaries = get_summaries(selected_course_id)
    if not summaries:
        st.info("该课程暂无笔记记录。")
    else:
        st.caption(f"共 {len(summaries)} 段笔记")

        latest_outline = _upsert_outline_if_needed(
            selected_course_id,
            summaries,
            assistant,
            can_call_llm=bool(api_key and assistant),
        )

        st.markdown("### 📚 自动合并课程大纲")
        if latest_outline:
            st.markdown(latest_outline["content"])
            st.caption(f"最近更新时间：{latest_outline['created_at'][:19]}")
        elif not api_key:
            st.info("输入 API Key 后可自动合并分段笔记并生成课程大纲。")
        else:
            st.info("暂无可展示的大纲。")

        st.divider()
        st.markdown("### 🧾 分段笔记")
        for s in summaries:
            with st.expander(f"第 {s['segment_index']} 段 — {s['created_at'][:16]}", expanded=False):
                st.markdown(s["content"])

# ---- Tab 2: 边学边测闭环 ----
with tab_quiz:
    summaries = get_summaries(selected_course_id)
    all_notes = "\n\n".join([s["content"] for s in summaries])

    if not api_key:
        st.warning("请在侧边栏输入 API Key 以生成结构化测验。")
    elif not all_notes.strip():
        st.info("该课程暂无笔记，无法生成测验。")
    else:
        num_q = st.slider("题目数量", 3, 10, 5)
        gen_key = f"quiz_data_{selected_course_id}"

        if st.button("生成结构化测验", type="primary"):
            with st.spinner("正在生成可自动判分测验..."):
                data = assistant.generate_quiz_structured(all_notes, num_q)
                st.session_state[gen_key] = data

        quiz_data = st.session_state.get(gen_key, {"questions": []})
        questions = quiz_data.get("questions", []) if isinstance(quiz_data, dict) else []

        if questions:
            st.markdown("### 作答区")
            for i, q in enumerate(questions):
                st.markdown(f"**Q{i + 1}. {q['question']}**")
                st.caption(f"知识点：{q.get('knowledge_point', '核心知识点')}")
                options = q.get("options", [])
                st.radio(
                    "请选择一个选项",
                    options,
                    key=f"quiz_{selected_course_id}_{i}",
                    index=None,
                    label_visibility="collapsed",
                )

            if st.button("提交并分析", type="secondary"):
                total = len(questions)
                correct = 0
                wrong_details = []

                for i, q in enumerate(questions):
                    selected_option = st.session_state.get(f"quiz_{selected_course_id}_{i}")
                    selected_label = _answer_label(selected_option, 0) if selected_option else ""
                    answer_label = (q.get("answer") or "A").strip().upper()[:1]

                    if selected_label == answer_label:
                        correct += 1
                    else:
                        wrong_map = q.get("wrong_causes", {}) if isinstance(q.get("wrong_causes"), dict) else {}
                        wrong_details.append(
                            {
                                "question": q.get("question", ""),
                                "knowledge_point": q.get("knowledge_point", "核心知识点"),
                                "selected": selected_label or "未作答",
                                "correct": answer_label,
                                "wrong_cause": wrong_map.get(selected_label, "概念理解偏差"),
                                "explanation": q.get("explanation", ""),
                            }
                        )

                add_quiz_attempt(selected_course_id, total, correct, wrong_details)
                st.success(f"本次得分：{correct}/{total}，正确率 {round(correct * 100 / max(total, 1), 1)}%")

                if wrong_details:
                    st.markdown("#### 错因分析")
                    for w in wrong_details:
                        with st.expander(f"{w['knowledge_point']} | 你的答案: {w['selected']} | 正确答案: {w['correct']}"):
                            st.markdown(f"- 错因：{w['wrong_cause']}")
                            st.markdown(f"- 解析：{w['explanation']}")

                st.rerun()

    attempts = get_quiz_attempts(selected_course_id)
    if attempts:
        st.divider()
        st.markdown("### 掌握度追踪")
        trend = []
        for idx, a in enumerate(attempts, 1):
            total = max(int(a.get("total_questions", 1)), 1)
            correct = int(a.get("correct_questions", 0))
            trend.append({"attempt": idx, "accuracy": round(correct * 100 / total, 1)})
        st.line_chart(trend, x="attempt", y="accuracy")

        weak_points = _build_weak_points(attempts)
        if weak_points:
            st.markdown("### 当前薄弱点")
            st.dataframe(weak_points, use_container_width=True)

# ---- Tab 3: 历史问答 (带 FAISS 检索) ----
with tab_qa:
    history = get_chat_history(selected_course_id)
    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    vector_store = None
    faiss_dir = get_faiss_dir(selected_course_id)
    if os.path.exists(os.path.join(faiss_dir, "index.faiss")):
        if api_key and assistant:
            try:
                vector_store = FAISS.load_local(
                    faiss_dir,
                    assistant.embeddings,
                    allow_dangerous_deserialization=True,
                )
            except Exception as e:
                st.warning(f"加载向量索引失败: {e}")

    if prompt := st.chat_input("基于历史笔记提问..."):
        if not api_key:
            st.error("请输入 API Key")
        else:
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.spinner("正在检索并思考..."):
                    started = datetime.now()
                    retrieved_parts = []
                    scores = []
                    pairs = _retrieve_docs_with_scores(vector_store, prompt, k=4)
                    pairs = _filter_docs_by_source(
                        pairs,
                        allowed_sources=allowed_sources,
                        max_score=max_retrieval_score if strict_evidence_mode else None,
                    )
                    for d, score in pairs:
                        page = d.metadata.get("page", "?")
                        retrieved_parts.append(f"[教材第{page}页] {d.page_content}")
                        if score is not None:
                            scores.append(float(score))
                    retrieved_context = "\n---\n".join(retrieved_parts)

                    summaries = get_summaries(selected_course_id)
                    notes_ctx = "\n".join([s["content"] for s in summaries]) if "notes" in allowed_sources else ""
                    full_ctx = f"{retrieved_context}\n\n【历史笔记】：\n{notes_ctx}"

                    has_evidence = bool(retrieved_parts or notes_ctx.strip())
                    if strict_evidence_mode and not has_evidence:
                        ans = "当前未检索到可信证据（教材或课堂笔记），为避免幻觉，本次暂不直接作答。请先上传教材或补充课堂记录。"
                    else:
                        ans = assistant.ask_question(prompt, history, full_ctx, trusted_domains=trusted_domains)

                    citation_ok = ("教材第" in ans) or ("课堂笔记" in ans) or (not has_evidence)
                    response_ms = int((datetime.now() - started).total_seconds() * 1000)
                    st.markdown(ans)

            add_chat_message(selected_course_id, "user", prompt)
            add_chat_message(selected_course_id, "assistant", ans)
            add_qa_metric(
                selected_course_id,
                prompt,
                has_evidence=has_evidence,
                used_doc_count=len(retrieved_parts),
                avg_retrieval_score=(sum(scores) / len(scores)) if scores else None,
                citation_ok=citation_ok,
                response_ms=response_ms,
                source_scope=",".join(allowed_sources),
            )
            st.rerun()

# ---- Tab 4: 知识图谱 ----
with tab_kg:
    kg_data = get_kg_graph(selected_course_id)
    if not kg_data["nodes"]:
        st.info("该课程暂无知识图谱数据。知识图谱会在课堂录制生成笔记时自动构建。")
    else:
        st.caption(f"共 {len(kg_data['nodes'])} 个概念节点，{len(kg_data['edges'])} 条关系边")

        G = nx.DiGraph()
        category_colors = {
            "concept": "#4FC3F7",
            "formula": "#FF8A65",
            "example": "#81C784",
        }
        for node in kg_data["nodes"]:
            color = category_colors.get(node.get("category", "concept"), "#90A4AE")
            G.add_node(node["label"], color=color, title=node.get("category", "concept"))
        for edge in kg_data["edges"]:
            G.add_edge(edge["source"], edge["target"], title=edge.get("relation", ""))

        net = Network(height="500px", width="100%", directed=True, bgcolor="#1e1e1e", font_color="white")
        net.from_nx(G)
        net.set_options(
            """
        {
            "physics": {
                "forceAtlas2Based": {
                    "gravitationalConstant": -50,
                    "centralGravity": 0.01,
                    "springLength": 100
                },
                "solver": "forceAtlas2Based"
            },
            "edges": {
                "arrows": {"to": {"enabled": true}},
                "font": {"size": 10, "color": "#aaaaaa"}
            },
            "nodes": {
                "font": {"size": 14}
            }
        }
        """
        )

        html_content = net.generate_html()
        components.html(html_content, height=520, scrolling=True)

# ---- Tab 5: 个性化复习清单 ----
with tab_plan:
    attempts = get_quiz_attempts(selected_course_id)
    weak_points = _build_weak_points(attempts)

    if not weak_points:
        st.info("先完成至少一次测验，系统会根据错因自动生成复习清单。")
    else:
        st.markdown("### 薄弱点汇总")
        st.dataframe(weak_points, use_container_width=True)

        if not api_key:
            st.warning("输入 API Key 后可生成结构化复习清单。")
        else:
            if st.button("生成个性化复习清单", type="primary"):
                weak_text_lines = [
                    f"- 薄弱点: {w['weak_point']} | 错误次数: {w['wrong_count']} | 主要错因: {w['main_cause']}"
                    for w in weak_points
                ]
                weak_text = "\n".join(weak_text_lines)

                with st.spinner("正在生成复习清单..."):
                    plan_items = assistant.build_review_plan(weak_text)
                    for item in plan_items:
                        due_date = (datetime.now() + timedelta(days=item.get("due_in_days", 1))).date().isoformat()
                        item["due_date"] = due_date

                    replace_review_plan_items(selected_course_id, plan_items)

                    # 每次重建清单时同步提醒，避免重复积累
                    delete_pending_plan_reminders(selected_course_id)
                    for item in get_review_plan_items(selected_course_id, status="pending"):
                        add_reminder(
                            selected_course_id,
                            "review_plan",
                            f"复习提醒：{item['weak_point']}（预计{item['estimated_minutes']}分钟）",
                            f"{item['due_date']}T20:00:00",
                            related_item_id=item["id"],
                        )
                st.success("已生成复习清单并创建主动提醒。")
                st.rerun()

    plan_items = get_review_plan_items(selected_course_id)
    if plan_items:
        st.divider()
        st.markdown("### 复习任务表")
        st.dataframe(
            [
                {
                    "id": p["id"],
                    "weak_point": p["weak_point"],
                    "recommended_material": p["recommended_material"],
                    "estimated_minutes": p["estimated_minutes"],
                    "priority": p["priority"],
                    "due_date": p["due_date"],
                    "status": p["status"],
                }
                for p in plan_items
            ],
            use_container_width=True,
        )

        st.markdown("### 任务状态更新")
        for p in plan_items:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(
                    f"- [{p['status']}] {p['weak_point']} | 材料: {p['recommended_material']} | 截止: {p['due_date']}"
                )
            with col2:
                if p["status"] == "pending":
                    if st.button("完成", key=f"done_plan_{p['id']}"):
                        update_review_plan_item_status(p["id"], "done")
                        st.rerun()

# ---- Tab 6: 材料推荐 ----
with tab_materials:
    attempts = get_quiz_attempts(selected_course_id)
    weak_points = _build_weak_points(attempts)

    if not weak_points:
        st.info("完成测验后可根据薄弱点自动推荐材料。")
    elif not api_key:
        st.warning("请先输入 API Key 以生成材料推荐。")
    else:
        if st.button("生成材料推荐", type="primary"):
            weak_text = "\n".join(
                [f"- {w['weak_point']}（错误次数{w['wrong_count']}，主要错因：{w['main_cause']}）" for w in weak_points]
            )
            with st.spinner("正在推荐材料..."):
                mats = assistant.recommend_materials(weak_text, trusted_domains=trusted_domains)
                st.session_state[f"materials_{selected_course_id}"] = mats

        mats = st.session_state.get(f"materials_{selected_course_id}", [])
        if mats:
            st.markdown("### 推荐结果")
            st.dataframe(mats, use_container_width=True)
            for m in mats:
                with st.expander(f"{m['title']} | {m['material_type']} | {m['estimated_minutes']} 分钟"):
                    st.markdown(f"- 推荐理由：{m['reason']}")
                    st.markdown(f"- 来源建议：{m['source_hint']}")

# ---- Tab 7: 主动提醒 ----
with tab_reminders:
    reminders = get_pending_reminders(selected_course_id)
    if not reminders:
        st.info("暂无待处理提醒。")
    else:
        st.markdown("### 待处理提醒")
        for r in reminders:
            due_time = r["due_at"].replace("T", " ")[:16]
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"- {r['content']} | 到期: {due_time}")
            with col2:
                if st.button("已处理", key=f"done_reminder_{r['id']}"):
                    mark_reminder_done(r["id"])
                    st.rerun()

    # 兼容保留：学习报告按钮
    st.divider()
    st.markdown("### 📊 个性化学习报告")
    if not api_key:
        st.warning("请在侧边栏输入 API Key 以生成学习报告。")
    else:
        if st.button("生成个性化学习报告", type="primary"):
            summaries = get_summaries(selected_course_id)
            all_notes = "\n\n".join([s["content"] for s in summaries])
            history = get_chat_history(selected_course_id)

            if not all_notes.strip():
                st.warning("该课程没有笔记内容，无法生成报告。")
            else:
                with st.spinner("正在分析学习数据并生成报告..."):
                    report = assistant.generate_report(all_notes, history)
                    st.session_state["last_report"] = report
                    st.session_state["last_report_course"] = selected_course_id

        if "last_report" in st.session_state and st.session_state.get("last_report_course") == selected_course_id:
            report_md = st.session_state["last_report"]
            st.markdown(report_md)
            pdf_bytes = _report_markdown_to_pdf_bytes(
                report_md,
                title=f"SkimClass Report - Course {selected_course_id}",
            )

            st.divider()
            st.markdown("### 导出与分享")
            export_col1, export_col2, export_col3, export_col4 = st.columns(4)
            with export_col1:
                st.download_button(
                    "导出 Markdown",
                    data=report_md.encode("utf-8"),
                    file_name=f"report_course_{selected_course_id}.md",
                    mime="text/markdown",
                )
            with export_col2:
                st.download_button(
                    "导出 PDF",
                    data=pdf_bytes,
                    file_name=f"report_course_{selected_course_id}.pdf",
                    mime="application/pdf",
                )
            with export_col3:
                bundle = _zip_report_bundle(
                    selected_label.split(" (")[0],
                    report_md,
                    get_summaries(selected_course_id),
                    get_review_plan_items(selected_course_id),
                    get_quiz_attempts(selected_course_id),
                )
                st.download_button(
                    "导出分享包 ZIP",
                    data=bundle,
                    file_name=f"skimclass_bundle_{selected_course_id}.zip",
                    mime="application/zip",
                )
            with export_col4:
                if st.button("保存到本地导出目录"):
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    export_dir = os.path.join(get_exports_dir(), str(selected_course_id))
                    os.makedirs(export_dir, exist_ok=True)
                    md_path = os.path.join(export_dir, f"report_{ts}.md")
                    pdf_path = os.path.join(export_dir, f"report_{ts}.pdf")
                    with open(md_path, "w", encoding="utf-8") as f:
                        f.write(report_md)
                    with open(pdf_path, "wb") as f:
                        f.write(pdf_bytes)
                    st.success(f"已保存：{md_path} 和 {pdf_path}")

            expire_hours = st.selectbox("分享链接有效期", [24, 72, 168], index=1)
            base_share_url = st.text_input("分享基础 URL", value=DEFAULT_PUBLIC_BASE_URL)
            if st.button("生成分享链接"):
                expires_at = (datetime.now() + timedelta(hours=int(expire_hours))).isoformat()
                token = create_shared_report(selected_course_id, report_md, expires_at)
                st.session_state["latest_share_link"] = _build_share_link(token, base_share_url)

            if st.session_state.get("latest_share_link"):
                st.code(st.session_state["latest_share_link"], language="text")
                st.caption("已生成可直接分享的完整链接；若未配置基础 URL，将显示相对链接参数。")


# ---- Tab 8: 评测指标 ----
with tab_metrics:
    qa_metrics = get_qa_metrics(selected_course_id)
    quiz_attempts = get_quiz_attempts(selected_course_id)
    plan_items = get_review_plan_items(selected_course_id)

    if not qa_metrics and not quiz_attempts:
        st.info("当前课程评测数据较少。先进行几次问答和测验后可查看完整指标。")
    else:
        qa_count = len(qa_metrics)
        evidence_hits = sum(int(m.get("has_evidence", 0)) for m in qa_metrics)
        citation_hits = sum(int(m.get("citation_ok", 0)) for m in qa_metrics)
        avg_ms = int(sum(int(m.get("response_ms", 0) or 0) for m in qa_metrics) / qa_count) if qa_count else 0

        quiz_count = len(quiz_attempts)
        avg_quiz_acc = 0.0
        if quiz_count:
            accs = []
            for q in quiz_attempts:
                total = max(int(q.get("total_questions", 1)), 1)
                correct = int(q.get("correct_questions", 0))
                accs.append(correct * 100.0 / total)
            avg_quiz_acc = round(sum(accs) / len(accs), 1)

        total_tasks = len(plan_items)
        done_tasks = sum(1 for p in plan_items if p.get("status") == "done")
        completion_rate = round(done_tasks * 100.0 / total_tasks, 1) if total_tasks else 0.0

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("问答证据命中率", f"{round(evidence_hits * 100.0 / qa_count, 1) if qa_count else 0}%")
        with c2:
            st.metric("问答引用合规率", f"{round(citation_hits * 100.0 / qa_count, 1) if qa_count else 0}%")
        with c3:
            st.metric("平均响应时延", f"{avg_ms} ms")
        with c4:
            st.metric("复习任务完成率", f"{completion_rate}%")

        st.divider()
        st.markdown("### 趋势图")

        if qa_metrics:
            qa_trend = []
            for idx, m in enumerate(qa_metrics, 1):
                qa_trend.append(
                    {
                        "qa_index": idx,
                        "has_evidence": int(m.get("has_evidence", 0)),
                        "citation_ok": int(m.get("citation_ok", 0)),
                        "response_ms": int(m.get("response_ms", 0) or 0),
                    }
                )
            st.line_chart(qa_trend, x="qa_index", y=["has_evidence", "citation_ok"])
            st.line_chart(qa_trend, x="qa_index", y="response_ms")

        if quiz_attempts:
            quiz_trend = []
            for idx, q in enumerate(quiz_attempts, 1):
                total = max(int(q.get("total_questions", 1)), 1)
                correct = int(q.get("correct_questions", 0))
                quiz_trend.append({"attempt": idx, "accuracy": round(correct * 100.0 / total, 1)})
            st.line_chart(quiz_trend, x="attempt", y="accuracy")

        st.divider()
        st.markdown("### 指标明细")
        if qa_metrics:
            st.dataframe(
                [
                    {
                        "id": m.get("id"),
                        "question": m.get("question", "")[:80],
                        "has_evidence": m.get("has_evidence"),
                        "used_doc_count": m.get("used_doc_count"),
                        "avg_retrieval_score": m.get("avg_retrieval_score"),
                        "citation_ok": m.get("citation_ok"),
                        "response_ms": m.get("response_ms"),
                        "source_scope": m.get("source_scope"),
                        "created_at": (m.get("created_at") or "")[:19],
                    }
                    for m in qa_metrics
                ],
                use_container_width=True,
            )

        st.caption(f"测验平均正确率：{avg_quiz_acc}% | 测验次数：{quiz_count} | 复习任务：{done_tasks}/{total_tasks}")
