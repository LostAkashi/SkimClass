import streamlit as st
import os
import time
import subprocess
from datetime import datetime
import fitz  # PyMuPDF
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from auto_capture import SegmentRecorder, get_available_microphones, purge_segment_media, cleanup_old_recordings, clear_raw_media
from agent import LangChainAssistant
from db import (
    init_db,
    create_course,
    add_summary,
    add_chat_message,
    update_course_faiss_flag,
    add_kg_nodes,
    add_kg_edges,
    get_pending_reminders,
    add_qa_metric,
    get_trusted_sources,
    replace_trusted_sources,
)
from paths import get_faiss_dir, get_recordings_dir

DEFAULT_RETENTION_HOURS = int(os.getenv("SKIM_RETENTION_HOURS", "24"))
DEFAULT_PURGE_RAW = os.getenv("SKIM_PURGE_RAW_AFTER_PROCESS", "1") != "0"
DEFAULT_RETRY_LIMIT = int(os.getenv("SKIM_SEGMENT_RETRY_LIMIT", "2"))
DEFAULT_AUTO_START_TIME = os.getenv("SKIM_AUTO_START_TIME", "08:00")
DEFAULT_PUBLIC_WIFI_HINT = os.getenv("SKIM_AUTO_START_WIFI_HINT", "")
DEFAULT_TRUSTED_DOMAINS = [x.strip().lower() for x in os.getenv("SKIM_TRUSTED_SOURCES", "").split(",") if x.strip()]

# 启动时幂等建表
init_db()

st.set_page_config(layout="wide", page_title="SkimClass - 专业 RAG 助教", page_icon="🎓")

# === 状态初始化 ===
if "recorder" not in st.session_state:
    st.session_state.recorder = None
if "summaries" not in st.session_state:
    st.session_state.summaries = []
if "processed_segs" not in st.session_state:
    st.session_state.processed_segs = set()
if "vector_store" not in st.session_state:
    st.session_state.vector_store = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "current_course_id" not in st.session_state:
    st.session_state.current_course_id = None
if "notified_reminders" not in st.session_state:
    st.session_state.notified_reminders = set()
if "processing_segments" not in st.session_state:
    st.session_state.processing_segments = False
if "segment_failures" not in st.session_state:
    st.session_state.segment_failures = {}
if "last_cleanup_ts" not in st.session_state:
    st.session_state.last_cleanup_ts = 0.0
if "auto_start_today_key" not in st.session_state:
    st.session_state.auto_start_today_key = ""


def _retrieve_docs_with_scores(vector_store, query, k=4):
    if not vector_store:
        return []
    try:
        pairs = vector_store.similarity_search_with_score(query, k=k)
        return pairs
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


def _get_current_wifi_ssid():
    """尽量获取当前连接 Wi-Fi SSID（macOS 优先）。"""
    airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
    if os.path.exists(airport):
        try:
            out = subprocess.check_output([airport, "-I"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                if " SSID:" in line and "BSSID" not in line:
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return ""


def _parse_hhmm(value):
    try:
        h, m = str(value).strip().split(":", 1)
        hh = max(0, min(23, int(h)))
        mm = max(0, min(59, int(m)))
        return hh, mm
    except Exception:
        return 8, 0


def _start_course_capture(capture_mode, course_name, interval, mic_choice):
    if capture_mode == "light":
        course_id = create_course(course_name, "light_mode_no_raw_capture")
        st.session_state.current_course_id = course_id
        st.session_state.recorder = None
        st.info("轻模式已开启：不会采集原始音视频，仅基于资料进行问答与复习。")
    else:
        recorder = SegmentRecorder(
            course_name,
            interval,
            device_index=mic_choice,
            capture_mode=capture_mode,
            audio_enabled=True,
            screen_enabled=True,
            capture_interval=1.0 if capture_mode == "enhanced" else None,
            diff_threshold=6 if capture_mode == "enhanced" else None,
        )
        course_id = create_course(course_name, recorder.base_dir)
        st.session_state.current_course_id = course_id
        st.session_state.recorder = recorder
        st.session_state.recorder.start()

    if st.session_state.vector_store and st.session_state.current_course_id:
        faiss_dir = get_faiss_dir(st.session_state.current_course_id)
        st.session_state.vector_store.save_local(faiss_dir)
        update_course_faiss_flag(st.session_state.current_course_id, True)


def _clear_all_raw_media_now():
    root = get_recordings_dir()
    if not os.path.exists(root):
        return 0
    removed = 0
    for name in os.listdir(root):
        base = os.path.join(root, name)
        if os.path.isdir(base):
            removed += clear_raw_media(base)
    return removed


def process_segments(
    base_dir,
    api_key,
    summary_model="qwen3-omni-flash",
    embedding_model="text-embedding-v4",
    purge_raw_after_process=True,
    failure_retry_limit=2,
):
    """遍历目录下所有 ready.flag 切片并生成总结。
    返回值表示是否有新切片被处理过。
    """
    processed_any = False
    if not base_dir or not os.path.exists(base_dir):
        return processed_any
    seg_dirs = sorted([d for d in os.listdir(base_dir) if d.startswith("seg_")])
    for d in seg_dirs:
        seg_path = os.path.join(base_dir, d)
        if not os.path.exists(os.path.join(seg_path, "ready.flag")):
            continue
        if seg_path in st.session_state.processed_segs:
            continue
        if os.path.exists(os.path.join(seg_path, "processed.flag")):
            st.session_state.processed_segs.add(seg_path)
            continue

        if os.path.exists(os.path.join(seg_path, "failed.flag")):
            fail_cnt = st.session_state.segment_failures.get(seg_path, 0)
            if fail_cnt >= failure_retry_limit:
                st.session_state.processed_segs.add(seg_path)
                continue

        try:
            processed_any = True
            st.toast(f"RAG 深度分析中: {d}...")
            audio_p = os.path.join(seg_path, "audio.wav")

            # 解析 images.txt（新格式: 时间戳\t路径）
            img_p_list = []
            img_timestamps = []
            if os.path.exists(os.path.join(seg_path, "images.txt")):
                with open(os.path.join(seg_path, "images.txt"), "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        if "\t" in line:
                            ts_str, path = line.split("\t", 1)
                            try:
                                img_timestamps.append(float(ts_str))
                            except ValueError:
                                img_timestamps.append(0.0)
                            img_p_list.append(path)
                        else:
                            # 兼容旧格式（仅路径）
                            img_p_list.append(line)
                            img_timestamps.append(0.0)

            assistant = LangChainAssistant(api_key, summary_model, embedding_model)
            report = assistant.analyze_segment(
                audio_p, img_p_list, st.session_state.vector_store,
                image_timestamps=img_timestamps if img_timestamps else None
            )

            st.session_state.summaries.append({"content": report})
            st.session_state.processed_segs.add(seg_path)

            # 计算时间范围
            start_time = min(img_timestamps) if img_timestamps else None
            end_time = max(img_timestamps) if img_timestamps else None

            # 持久化笔记到数据库
            if st.session_state.current_course_id:
                seg_index = int(d.split("_")[1]) if "_" in d else 0
                add_summary(st.session_state.current_course_id, seg_index, report,
                            start_time=start_time, end_time=end_time)

                # 提取知识图谱并持久化
                try:
                    kg_data = assistant.extract_knowledge_graph(report)
                    if kg_data.get("nodes"):
                        add_kg_nodes(st.session_state.current_course_id, kg_data["nodes"])
                    if kg_data.get("edges"):
                        add_kg_edges(st.session_state.current_course_id, kg_data["edges"])
                except Exception as e:
                    print(f"知识图谱提取/保存失败: {e}")

            if purge_raw_after_process:
                purge_segment_media(seg_path)

            with open(os.path.join(seg_path, "processed.flag"), "w") as f:
                f.write(datetime.now().isoformat())
            fail_flag = os.path.join(seg_path, "failed.flag")
            if os.path.exists(fail_flag):
                try:
                    os.remove(fail_flag)
                except Exception:
                    pass

        except Exception as e:
            fail_cnt = st.session_state.segment_failures.get(seg_path, 0) + 1
            st.session_state.segment_failures[seg_path] = fail_cnt
            with open(os.path.join(seg_path, "failed.flag"), "w") as f:
                f.write(f"{datetime.now().isoformat()}\n{str(e)}")
            if fail_cnt >= failure_retry_limit:
                st.session_state.processed_segs.add(seg_path)
            st.warning(f"分段 {d} 处理失败（第{fail_cnt}次）：{e}")

    return processed_any


# === 侧边栏 ===
with st.sidebar:
    st.header("⚙️ RAG 系统配置")
    api_key = st.text_input("🔑 API Key", type="password")

    st.divider()
    st.subheader("🤖 模型配置")
    summary_model = st.text_input("总结模型", value="qwen3-omni-flash")
    embedding_model = st.text_input("向量模型", value="text-embedding-v4")

    st.divider()
    course_name = st.text_input("课程名称", "高等数学")
    capture_mode = st.selectbox(
        "🎚️ 采集模式",
        ["light", "standard", "enhanced"],
        index=2,
        format_func=lambda x: {"light": "轻模式（不录音不截图，仅资料与问答）", "standard": "标准模式（录音+关键帧）", "enhanced": "增强模式（高频关键帧+录音）"}[x],
    )
    interval = st.number_input("⏱️ 总结间隔 (分钟)", min_value=1, value=1)
    purge_raw_after_process = st.checkbox("处理后删除原始音视频", value=DEFAULT_PURGE_RAW)
    retention_hours = st.number_input(
        "原始数据保留时长(小时)",
        min_value=1,
        max_value=168,
        value=min(max(DEFAULT_RETENTION_HOURS, 1), 168),
    )
    failure_retry_limit = st.number_input(
        "分段失败重试次数",
        min_value=1,
        max_value=5,
        value=min(max(DEFAULT_RETRY_LIMIT, 1), 5),
    )

    st.divider()
    st.subheader("🚦 自动启动")
    auto_start_enabled = st.checkbox("按计划自动开始采集", value=False)
    auto_start_time = st.text_input("自动开始时间(HH:MM)", value=DEFAULT_AUTO_START_TIME)
    auto_days = st.multiselect(
        "自动启动星期",
        ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
        default=["周一", "周二", "周三", "周四", "周五"],
    )
    wifi_hint = st.text_input("Wi-Fi 名称包含(可选)", value=DEFAULT_PUBLIC_WIFI_HINT)

    st.divider()
    st.subheader("🧹 数据治理")
    if st.button("一键清除全部原始音视频", use_container_width=True):
        removed = _clear_all_raw_media_now()
        st.success(f"已清除原始媒体文件 {removed} 个")

    st.divider()
    st.subheader("🧾 可信问答策略")
    allowed_sources = st.multiselect(
        "允许引用来源",
        ["textbook", "notes"],
        default=["textbook", "notes"],
    )
    strict_evidence_mode = st.checkbox("低置信度时拒答并提示补充依据", value=True)
    max_retrieval_score = st.number_input("检索最大距离阈值（越小越严格）", min_value=0.1, max_value=5.0, value=1.2, step=0.1)

    st.caption("教师可信来源白名单（域名，一行一个）")
    current_whitelist = get_trusted_sources(st.session_state.current_course_id) if st.session_state.current_course_id else DEFAULT_TRUSTED_DOMAINS
    whitelist_text = st.text_area("白名单域名", value="\n".join(current_whitelist), height=90)
    if st.session_state.current_course_id and st.button("保存白名单"):
        domains = [x.strip().lower() for x in whitelist_text.replace(",", "\n").splitlines() if x.strip()]
        replace_trusted_sources(st.session_state.current_course_id, domains)
        st.success("已更新可信来源白名单")

    st.divider()
    st.subheader("⏰ 主动提醒")
    reminder_course_id = st.session_state.current_course_id
    reminders = get_pending_reminders(reminder_course_id)
    if reminders:
        now = datetime.now()
        show_count = min(3, len(reminders))
        for r in reminders[:show_count]:
            due_dt = None
            try:
                due_dt = datetime.fromisoformat(r["due_at"])
            except Exception:
                pass

            due_text = r["due_at"].replace("T", " ")[:16]
            st.caption(f"- {r['content']} | 截止 {due_text}")

            # 对到期提醒只提示一次，避免每次刷新都弹窗。
            if due_dt and due_dt <= now and r["id"] not in st.session_state.notified_reminders:
                st.toast(f"复习提醒：{r['content']}")
                st.session_state.notified_reminders.add(r["id"])
    else:
        st.caption("暂无待处理提醒")

    # 麦克风选择
    mic_choice = None
    mics = get_available_microphones()
    if mics:
        options = {f"{d['name']} (#{d['index']})": d['index'] for d in mics}
        sel = st.selectbox("🎤 录音设备", list(options.keys()))
        mic_choice = options[sel]
    else:
        st.warning("未检测到可用麦克风，录音可能失败。")

    # --- 核心 RAG 逻辑：PDF 处理 ---
    uploaded_pdf = st.file_uploader("📚 投喂教材 (构建向量索引)", type=["pdf"])
    if uploaded_pdf and st.session_state.vector_store is None:
        if not api_key:
            st.warning("请先输入 API Key 以初始化 Embedding 模型")
        else:
            with st.spinner("正在构建 RAG 向量索引..."):
                try:
                    # 1. 按页提取文本（保留页码元数据用于溯源）
                    pdf_doc = fitz.open(stream=uploaded_pdf.read(), filetype="pdf")
                    text_splitter = RecursiveCharacterTextSplitter(
                        chunk_size=500,
                        chunk_overlap=50,
                        length_function=len
                    )

                    all_documents = []
                    for page_num, page in enumerate(pdf_doc, 1):
                        page_text = page.get_text().strip()
                        if not page_text:
                            continue
                        page_chunks = text_splitter.split_text(page_text)
                        for chunk in page_chunks:
                            chunk = str(chunk).strip()
                            if chunk:
                                all_documents.append(Document(
                                    page_content=chunk,
                                    metadata={"page": page_num, "source": "textbook"}
                                ))

                    if not all_documents:
                        st.error("❌ 未能从 PDF 中提取到有效文本，请检查文件内容是否为纯图片。")
                        st.stop()

                    # 2. 向量化并入库（使用 from_documents 保留元数据）
                    assistant = LangChainAssistant(api_key, summary_model, embedding_model)
                    vector_db = FAISS.from_documents(all_documents, assistant.embeddings)

                    st.session_state.vector_store = vector_db
                    st.success(f"成功构建索引：{len(all_documents)} 个知识切片（含页码元数据）")

                    # 4. 持久化 FAISS 索引到磁盘
                    if st.session_state.current_course_id:
                        faiss_dir = get_faiss_dir(st.session_state.current_course_id)
                        vector_db.save_local(faiss_dir)
                        update_course_faiss_flag(st.session_state.current_course_id, True)

                except Exception as e:
                    st.error(f"索引构建失败: {str(e)}")

    st.divider()
    if st.session_state.recorder is None or not st.session_state.recorder.is_recording:
        start_label = "🚀 开始课堂" if capture_mode == "light" else "🚀 开始录像"
        if st.button(start_label, type="primary", use_container_width=True):
            if not api_key:
                st.error("请先输入 API Key")
            else:
                _start_course_capture(capture_mode, course_name, interval, mic_choice)
                st.rerun()
    else:
        st.success("🎙️ 正在录制...")
        health = st.session_state.recorder.get_health_snapshot()
        alive_threads = health.get("threads", {})
        if alive_threads and not all(alive_threads.values()):
            st.warning(f"线程异常：{alive_threads}，建议停止后重启录制。")
        if health.get("errors"):
            st.caption(f"最近错误：{health['errors']}")
        if health.get("is_paused"):
            st.warning("当前已暂停采集")
            if st.button("▶️ 继续", use_container_width=True):
                st.session_state.recorder.resume()
                st.rerun()
        else:
            if st.button("⏸ 暂停", use_container_width=True):
                st.session_state.recorder.pause()
                st.rerun()

        if st.button("🗑 清除本次课堂原始媒体", use_container_width=True):
            removed = st.session_state.recorder.clear_raw_data()
            st.success(f"已清除本次原始媒体文件 {removed} 个")
            st.rerun()

        if st.button("⏹ 停止", type="secondary", use_container_width=True):
            st.session_state.recorder.stop()
            process_segments(
                st.session_state.recorder.base_dir,
                api_key,
                summary_model,
                embedding_model,
                purge_raw_after_process=purge_raw_after_process,
                failure_retry_limit=int(failure_retry_limit),
            )
            st.session_state.recorder = None
            st.rerun()

trusted_domains = get_trusted_sources(st.session_state.current_course_id) if st.session_state.current_course_id else DEFAULT_TRUSTED_DOMAINS

if auto_start_enabled and (st.session_state.recorder is None or not st.session_state.recorder.is_recording):
    now = datetime.now()
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    hh, mm = _parse_hhmm(auto_start_time)
    today_key = f"{now.date().isoformat()}_{capture_mode}"
    start_due = (now.hour, now.minute) >= (hh, mm)
    day_ok = weekday_names[now.weekday()] in (auto_days or [])
    ssid = _get_current_wifi_ssid()
    wifi_ok = True if not wifi_hint.strip() else (wifi_hint.strip().lower() in ssid.lower())
    if day_ok and start_due and wifi_ok and st.session_state.auto_start_today_key != today_key:
        if api_key:
            _start_course_capture(capture_mode, course_name, interval, mic_choice)
            st.session_state.auto_start_today_key = today_key
            st.toast(f"已按计划自动开启采集（Wi-Fi: {ssid or '未知'}）")
            st.rerun()

# === 主界面 ===
col_left, col_right = st.columns([5, 4])

# --- 左侧：实时总结 ---
with col_left:
    st.subheader(f"📝 {course_name} - 实时课程笔记 (RAG)")
    for sum_data in st.session_state.summaries:
        with st.chat_message("assistant", avatar="🤖"):
            st.markdown(sum_data["content"])

# --- 右侧：助教问答 ---
with col_right:
    tab_chat, tab_data = st.tabs(["💬 智能问答", "📂 原始数据"])
    with tab_chat:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if prompt := st.chat_input("向助教提问 (基于教材回答)..."):
            if not api_key:
                st.error("需要 API Key")
            else:
                st.session_state.chat_history.append({"role": "user", "content": prompt})
                # 持久化用户消息
                if st.session_state.current_course_id:
                    add_chat_message(st.session_state.current_course_id, "user", prompt)

                with st.chat_message("user"):
                    st.markdown(prompt)

                with st.chat_message("assistant"):
                    with st.spinner("正在检索教材并思考..."):
                        assistant = LangChainAssistant(api_key, summary_model, embedding_model)
                        started = time.time()

                        retrieved_context = ""
                        pairs = _retrieve_docs_with_scores(st.session_state.vector_store, prompt, k=4)
                        pairs = _filter_docs_by_source(
                            pairs,
                            allowed_sources=allowed_sources,
                            max_score=max_retrieval_score if strict_evidence_mode else None,
                        )
                        retrieved_parts = []
                        scores = []
                        for d, score in pairs:
                            page = d.metadata.get("page", "?")
                            retrieved_parts.append(f"[教材第{page}页] {d.page_content}")
                            if score is not None:
                                scores.append(float(score))
                        retrieved_context = "\n---\n".join(retrieved_parts)

                        # 加上当前笔记总结作为额外上下文
                        all_notes = "\n".join([s["content"] for s in st.session_state.summaries]) if "notes" in allowed_sources else ""
                        full_retrieval = f"{retrieved_context}\n\n【近期笔记】：\n{all_notes}"

                        has_evidence = bool(retrieved_parts or all_notes.strip())
                        if strict_evidence_mode and not has_evidence:
                            ans = "当前未检索到可信证据（教材或课堂笔记），为避免幻觉，本次暂不直接作答。请先上传教材或补充课堂记录。"
                        else:
                            ans = assistant.ask_question(
                                prompt,
                                st.session_state.chat_history,
                                full_retrieval,
                                trusted_domains=trusted_domains,
                            )

                        citation_ok = ("教材第" in ans) or ("课堂笔记" in ans) or (not has_evidence)
                        response_ms = int((time.time() - started) * 1000)
                        st.markdown(ans)
                        st.session_state.chat_history.append({"role": "assistant", "content": ans})
                        # 持久化助手消息
                        if st.session_state.current_course_id:
                            add_chat_message(st.session_state.current_course_id, "assistant", ans)
                            add_qa_metric(
                                st.session_state.current_course_id,
                                prompt,
                                has_evidence=has_evidence,
                                used_doc_count=len(retrieved_parts),
                                avg_retrieval_score=(sum(scores) / len(scores)) if scores else None,
                                citation_ok=citation_ok,
                                response_ms=response_ms,
                                source_scope=",".join(allowed_sources),
                            )

    with tab_data:
        st.caption(f"原始数据目录：{st.session_state.recorder.base_dir if st.session_state.recorder else '未开始录制'}")
        st.caption(f"可信来源白名单：{', '.join(trusted_domains) if trusted_domains else '未设置（仅教材/笔记）'}")
        if st.button("立即执行一次 TTL 清理", key="manual_ttl_cleanup"):
            removed = cleanup_old_recordings(int(retention_hours), exclude_dirs=None)
            st.success(f"已清理原始媒体文件 {removed} 个")


# === 后台自动分析 ===
# 如果正在录制则定期检查新切片
if st.session_state.recorder and st.session_state.recorder.is_recording:
    if not st.session_state.processing_segments:
        st.session_state.processing_segments = True
        try:
            if process_segments(
                st.session_state.recorder.base_dir,
                api_key,
                summary_model,
                embedding_model,
                purge_raw_after_process=purge_raw_after_process,
                failure_retry_limit=int(failure_retry_limit),
            ):
                st.rerun()
        finally:
            st.session_state.processing_segments = False

    now_ts = time.time()
    if now_ts - st.session_state.last_cleanup_ts > 300:
        active_dir = st.session_state.recorder.base_dir if st.session_state.recorder else None
        cleanup_old_recordings(int(retention_hours), exclude_dirs=[active_dir] if active_dir else None)
        st.session_state.last_cleanup_ts = now_ts

    time.sleep(5)
    st.rerun()
else:
    now_ts = time.time()
    if now_ts - st.session_state.last_cleanup_ts > 300:
        cleanup_old_recordings(int(retention_hours), exclude_dirs=None)
        st.session_state.last_cleanup_ts = now_ts
