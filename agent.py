import os
import base64
import json
from urllib.parse import urlparse
from dashscope import MultiModalConversation
from langchain_openai import ChatOpenAI
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_classic.memory import ConversationBufferWindowMemory

class LangChainAssistant:
    def __init__(self, api_key, summary_model="qwen3-omni-flash", embedding_model="text-embedding-v4"):
        self.api_key = api_key
        self.summary_model = summary_model
        self.llm_config = {
            "openai_api_key": api_key,
            "openai_api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1"
        }

        # 视觉、语音、对话统一使用用户选择的总结模型
        self.omni_llm = ChatOpenAI(model=summary_model, **self.llm_config)
        self.chat_llm = ChatOpenAI(model=summary_model, **self.llm_config)

        # 向量嵌入使用用户选择的向量模型
        self.embeddings = DashScopeEmbeddings(
            model=embedding_model,
            dashscope_api_key=api_key
        )

    def _encode_file(self, file_path):
        """通用的文件 Base64 编码器（支持图片和音频）"""
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')

    def _parse_json_response(self, text, fallback):
        """尽量把模型返回解析成 JSON，失败时返回 fallback。"""
        try:
            cleaned = (text or "").strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
            return json.loads(cleaned)
        except Exception:
            return fallback

    def _extract_domain(self, text):
        raw = str(text or "").strip()
        if not raw:
            return ""
        if "://" not in raw:
            raw = f"https://{raw}"
        try:
            return (urlparse(raw).hostname or "").lower()
        except Exception:
            return ""

    def analyze_segment(self, audio_path, image_paths, vector_store=None, image_timestamps=None):
        """
        实时总结模块：真实听取录音 -> 检索教材 -> 结合图片生成总结
        image_timestamps: list[float] 或 None，与 image_paths 一一对应的相对时间（秒）
        """
        # 1. 真实的音频转写 (调用 Qwen3-Omni-Flash 全模态能力)
        transcript = "(未能识别到有效语音)"
        if os.path.exists(audio_path):
            # 防止发送空文件导致 API 返回 400 URL 无效之类的错误
            try:
                size = os.path.getsize(audio_path)
            except Exception:
                size = 0
            if size < 100:
                transcript = "(未能录制到有效音频)"
                print(f"⚠️ 音频文件 {audio_path} 大小过小 ({size} 字节)，跳过转写")
            else:
                try:
                    # 使用 DashScope 原生 SDK 调用（支持本地文件路径，无需 base64）
                    abs_audio = os.path.abspath(audio_path)
                    messages = [{
                        "role": "user",
                        "content": [
                            {"text": "请仔细听这段课堂录音，并将其准确转化为文本转写内容。只需输出转写的文本，不要有其他废话。"},
                            {"audio": f"file://{abs_audio}"}
                        ]
                    }]
                    response = MultiModalConversation.call(
                        model=self.summary_model,
                        messages=messages,
                        api_key=self.api_key
                    )
                    if response.status_code == 200:
                        result_text = response.output.choices[0].message.content[0].get("text", "")
                        if result_text:
                            transcript = result_text
                    else:
                        transcript = f"(语音识别失败: {response.code} - {response.message})"
                        print(f"❌ 转写返回错误: {response.code} - {response.message}")
                except Exception as e:
                    transcript = f"(语音识别异常: {str(e)})"
                    print(f"❌ 转写调用失败: {e}")

        # 2. 执行 RAG：根据真实的转写内容检索教材（带页码溯源）
        pdf_context = ""
        if vector_store and transcript not in ["(未能识别到有效语音)", ""]:
            try:
                docs = vector_store.similarity_search(transcript, k=3)
                pdf_parts = []
                for doc in docs:
                    page = doc.metadata.get("page", "?")
                    pdf_parts.append(f"[教材第{page}页] {doc.page_content}")
                pdf_context = "\n---\n".join(pdf_parts)
            except Exception as e:
                pdf_context = f"(检索失败: {str(e)})"

        # 3. 构造多模态最终总结消息
        content = [{"type": "text", "text": "你是一个专业助教。请结合以下幻灯片截图、真实课堂录音转写，以及检索到的教材原件片段，生成结构化的课程总结。注意在总结中标注时间锚点（如 [03:25]）和教材来源页码。"}]

        # 添加有效图片（附带时间戳标注）
        valid_imgs = [p for p in image_paths if os.path.exists(p)][:4]
        for idx, img_p in enumerate(valid_imgs):
            # 标注截图对应的课堂时间
            ts_label = ""
            if image_timestamps and idx < len(image_timestamps):
                secs = image_timestamps[idx]
                mm, ss = int(secs // 60), int(secs % 60)
                ts_label = f" (截图时间: [{mm:02d}:{ss:02d}])"
            content.append({"type": "text", "text": f"幻灯片截图 {idx+1}{ts_label}："})
            base64_img = self._encode_file(img_p)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}
            })

        # 注入真实录音和 RAG 上下文
        full_info = f"【课堂录音转写】：\n{transcript}\n\n【教材参考 (RAG)】：\n{pdf_context}"
        content.append({"type": "text", "text": full_info})

        # 指令要求
        content.append({"type": "text", "text": """
        输出格式要求 (Markdown)：
        ## ⏱️ 本时段课程摘要
        请在每个知识点前标注对应的课堂时间锚点，格式如 [MM:SS]。
        如果引用了教材内容，请标注来源页码，如"根据教材第X页"。
        **1. 要点清单** (3-5条，按课堂逻辑排序)
        **2. 术语表** (术语 + 一句话解释 + 来源)
        **3. 本段待解问题** (1-2条)
        **4. 边学边测** (2-3道单选/判断题，每题含答案与解析)
        **5. 知识图谱** (概念层级与关系)
        """})

        try:
            response = self.omni_llm.invoke([HumanMessage(content=content)])
            return response.content
        except Exception as e:
            return f"❌ 实时总结异常: {str(e)}"

    def generate_quiz(self, notes_text, num_questions=5):
        """基于历史笔记内容生成复习测验题目，返回 Markdown 格式。"""
        prompt_text = f"""你是一位严谨的大学助教。请基于以下课堂笔记内容，生成 {num_questions} 道复习测验题。

要求：
- 包含选择题和简答题的混合
- 每道题后附上参考答案和解析
- 题目应覆盖笔记中的核心知识点
- 按难度递增排列

【课堂笔记内容】：
{notes_text}

请用以下 Markdown 格式输出：
## 复习测验
### 第 1 题 (选择题)
...
**参考答案**：...
**解析**：...
"""
        response = self.chat_llm.invoke([HumanMessage(content=prompt_text)])
        return response.content

    def generate_quiz_structured(self, notes_text, num_questions=5):
        """生成结构化测验题，供前端自动判分与错因分析。"""
        prompt_text = f"""你是一位课程助教。请基于课堂笔记生成 {num_questions} 道单选题。

必须返回 JSON，且只返回 JSON，不要额外文字。
JSON 格式如下：
{{
  "questions": [
    {{
      "question": "题干",
      "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
      "answer": "A",
      "knowledge_point": "知识点名称",
      "explanation": "正确答案解析",
      "wrong_causes": {{
        "A": "选择A时常见误区（若A是正确答案可写正确）",
        "B": "...",
        "C": "...",
        "D": "..."
      }}
    }}
  ]
}}

要求：
1. 题目覆盖不同核心知识点，难度递进；
2. 每题只有一个正确答案；
3. wrong_causes 要具体、有教学意义。

【课堂笔记】
{notes_text}
"""
        try:
            response = self.chat_llm.invoke([HumanMessage(content=prompt_text)])
            data = self._parse_json_response(response.content, {"questions": []})
            if not isinstance(data, dict):
                return {"questions": []}
            questions = data.get("questions") or []
            normalized = []
            for q in questions:
                options = q.get("options") or []
                if len(options) < 2:
                    continue
                answer = str(q.get("answer", "A")).strip().upper()[:1] or "A"
                wrong_causes = q.get("wrong_causes") or {}
                normalized.append({
                    "question": str(q.get("question", "未命名题目")).strip(),
                    "options": [str(op).strip() for op in options],
                    "answer": answer,
                    "knowledge_point": str(q.get("knowledge_point", "核心知识点")).strip(),
                    "explanation": str(q.get("explanation", "")).strip(),
                    "wrong_causes": {
                        "A": str(wrong_causes.get("A", "概念理解偏差")).strip(),
                        "B": str(wrong_causes.get("B", "概念理解偏差")).strip(),
                        "C": str(wrong_causes.get("C", "概念理解偏差")).strip(),
                        "D": str(wrong_causes.get("D", "概念理解偏差")).strip(),
                    }
                })
            return {"questions": normalized}
        except Exception:
            return {"questions": []}

    def build_course_outline(self, summaries):
        """将相邻分段笔记自动合并为课程大纲（主题-段落范围）。"""
        if not summaries:
            return "## 课程大纲\n暂无内容。"

        blocks = []
        for s in summaries:
            seg = s.get("segment_index", 0)
            txt = (s.get("content") or "").strip()
            if txt:
                blocks.append(f"[段落{seg}]\n{txt}")
        joined = "\n\n".join(blocks)

        prompt_text = f"""你是课程大纲整理助手。请把以下按时间顺序的分段课堂笔记自动合并为一份课程大纲。

要求：
1. 自动合并相邻且主题相近的段落；
2. 每个主题要标注来源段落范围（如 段落2-4）；
3. 输出 Markdown，结构如下：
## 课程大纲
### 主题1（段落x-y）
- 要点...
- 关键词...
### 主题2（段落...）

课堂笔记如下：
{joined}
"""
        try:
            response = self.chat_llm.invoke([HumanMessage(content=prompt_text)])
            return response.content
        except Exception as e:
            return f"## 课程大纲\n自动合并失败：{str(e)}"

    def build_review_plan(self, weak_points_text):
        """根据薄弱点生成结构化复习计划。"""
        prompt_text = f"""你是一位学习规划助教。请根据薄弱点信息，生成结构化复习清单。

只输出 JSON，格式：
{{
  "plan": [
    {{
      "weak_point": "薄弱点",
      "recommended_material": "推荐材料（教材页码/笔记段落/题型）",
      "estimated_minutes": 25,
      "priority": "high|medium|low",
      "due_in_days": 1,
      "source_note": "为什么要复习这个点"
    }}
  ]
}}

要求：
1. 每项 estimated_minutes 为 10-60 之间整数；
2. 优先级要和薄弱程度匹配；
3. due_in_days 只取 1/2/3/7。

【薄弱点数据】
{weak_points_text}
"""
        try:
            response = self.chat_llm.invoke([HumanMessage(content=prompt_text)])
            data = self._parse_json_response(response.content, {"plan": []})
            if not isinstance(data, dict):
                return []
            plan = data.get("plan") or []
            normalized = []
            for item in plan:
                minutes = int(item.get("estimated_minutes", 20))
                minutes = max(10, min(60, minutes))
                due = int(item.get("due_in_days", 1))
                if due not in (1, 2, 3, 7):
                    due = 1
                normalized.append({
                    "weak_point": str(item.get("weak_point", "未命名薄弱点")).strip(),
                    "recommended_material": str(item.get("recommended_material", "复习课堂笔记")).strip(),
                    "estimated_minutes": minutes,
                    "priority": str(item.get("priority", "medium")).strip().lower(),
                    "due_in_days": due,
                    "source_note": str(item.get("source_note", "来自最近测验与问答表现")).strip(),
                    "status": "pending",
                })
            return normalized
        except Exception:
            return []

    def recommend_materials(self, weak_points_text, trusted_domains=None):
        """生成结构化材料推荐列表。"""
        trusted_domains = [str(d).strip().lower() for d in (trusted_domains or []) if str(d).strip()]
        whitelist_text = "、".join(trusted_domains) if trusted_domains else "（未设置）"
        prompt_text = f"""你是一位课程资料推荐助手。请基于薄弱点给出精简、可执行的复习材料推荐。

只输出 JSON，格式：
{{
  "materials": [
    {{
      "title": "材料标题",
      "material_type": "教材|课堂笔记|练习题|拓展阅读",
      "reason": "推荐理由",
      "estimated_minutes": 20,
      "source_hint": "例如 教材第12页 或 第3段课堂笔记"
    }}
  ]
}}

要求：
1. 最多返回 8 条；
2. 每条都要有 estimated_minutes（10-60）；
3. reason 要点明对哪个薄弱点有效。
4. 若给出外部网站来源，source_hint 必须来自以下可信域名白名单：{whitelist_text}。
5. 若无法满足白名单，请优先推荐“教材/课堂笔记/练习题”并在 source_hint 中写教材页码或笔记段号。

【薄弱点信息】
{weak_points_text}
"""
        try:
            response = self.chat_llm.invoke([HumanMessage(content=prompt_text)])
            data = self._parse_json_response(response.content, {"materials": []})
            if not isinstance(data, dict):
                return []
            materials = data.get("materials") or []
            normalized = []
            for m in materials[:8]:
                minutes = int(m.get("estimated_minutes", 20))
                minutes = max(10, min(60, minutes))
                source_hint = str(m.get("source_hint", "课堂笔记")).strip()
                if trusted_domains:
                    domain = self._extract_domain(source_hint)
                    if domain and domain not in trusted_domains:
                        source_hint = "课堂笔记（白名单外来源已过滤）"
                normalized.append({
                    "title": str(m.get("title", "未命名材料")).strip(),
                    "material_type": str(m.get("material_type", "课堂笔记")).strip(),
                    "reason": str(m.get("reason", "用于巩固薄弱点")).strip(),
                    "estimated_minutes": minutes,
                    "source_hint": source_hint,
                })
            return normalized
        except Exception:
            return []

    def extract_knowledge_graph(self, notes_text):
        """从笔记文本中提取知识图谱的节点和边，返回 dict。"""
        prompt_text = f"""请从以下课堂笔记中提取知识图谱。识别关键概念（节点）及它们之间的关系（边）。

要求：
- 节点的 category 从以下选择：concept（概念）、formula（公式）、example（实例）
- 关系从以下选择：包含、前置知识、应用于、特殊形式、推导、等价
- 只输出 JSON，不要有其他文字

【课堂笔记】：
{notes_text}

请严格按以下 JSON 格式输出（不要包含 ```json 标记）：
{{"nodes": [{{"label": "概念名", "category": "concept"}}], "edges": [{{"source": "概念A", "target": "概念B", "relation": "包含"}}]}}"""

        try:
            response = self.chat_llm.invoke([HumanMessage(content=prompt_text)])
            import json
            text = response.content.strip()
            # 兼容模型可能输出的 ```json ... ``` 包裹
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            return json.loads(text)
        except Exception as e:
            print(f"❌ 知识图谱提取失败: {e}")
            return {"nodes": [], "edges": []}

    def generate_report(self, all_notes, chat_history_list):
        """基于全部笔记和问答历史，生成个性化学习报告。"""
        # 整理问答历史
        qa_text = ""
        for msg in chat_history_list:
            role_label = "学生提问" if msg["role"] == "user" else "助教回答"
            qa_text += f"【{role_label}】：{msg['content']}\n"

        prompt_text = f"""你是一位资深教育分析师。请基于以下课堂笔记和学生的问答记录，生成一份个性化学习分析报告。

【全部课堂笔记】：
{all_notes}

【问答历史】：
{qa_text if qa_text else '（暂无问答记录）'}

请按以下 Markdown 格式输出：

## 📊 个性化学习报告

### 1. 知识点掌握度分析
分析学生通过提问暴露出的知识盲区，以及笔记覆盖但学生未提问的知识点（可能已掌握）。

### 2. 薄弱环节识别
列出学生反复提问或理解困难的知识点，按严重程度排序。

### 3. 学习建议
- 针对每个薄弱环节，给出具体的复习建议
- 推荐应重点复习的笔记段落（引用具体内容）
- 建议的学习顺序和方法

### 4. 知识掌握度评估
对每个核心知识点给出掌握度评估（优秀/良好/一般/需加强），以表格形式呈现。
"""
        try:
            response = self.chat_llm.invoke([HumanMessage(content=prompt_text)])
            return response.content
        except Exception as e:
            return f"❌ 学习报告生成失败: {str(e)}"

    def ask_question(self, question, chat_history_list, retrieved_context, trusted_domains=None):
        """
        助教问答模块：专注于 Chain 的生成，接收已检索好的 Context
        """
        trusted_note = ""
        trusted_domains = [str(d).strip().lower() for d in (trusted_domains or []) if str(d).strip()]
        if trusted_domains:
            trusted_note = "\n4. 若需要补充外部资料，只能引用以下可信来源域名：" + ", ".join(trusted_domains) + "。"

        system_msg = (
            '你是一个助教。请基于以下提供的教材片段回答学生的问题。\n\n'
            '回答要求：\n'
            '1. 引用教材内容时，请标注来源，如"根据教材第X页..."。\n'
            '2. 引用课堂笔记时，请标注"根据第X段课堂笔记..."。\n'
            '3. 如果提供的片段中没有相关内容，请结合通用知识回答并明确说明'
            '"以下内容未在教材中找到对应出处"。'
            + trusted_note +
            '\n\n'
            '【检索到的教材内容】：\n{context}'
        )
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_msg),
            MessagesPlaceholder(variable_name="chat_history"),
            ("user", "{input}")
        ])

        memory = ConversationBufferWindowMemory(k=5, return_messages=True, memory_key="chat_history")
        for msg in chat_history_list:
            if msg["role"] == "user":
                memory.chat_memory.add_user_message(msg["content"])
            else:
                memory.chat_memory.add_ai_message(msg["content"])

        chain = prompt | self.chat_llm

        try:
            response = chain.invoke({
                "context": retrieved_context,
                "chat_history": memory.load_memory_variables({})["chat_history"],
                "input": question
            })
            return response.content
        except Exception as e:
            return f"❌ 助教回答错误: {str(e)}"