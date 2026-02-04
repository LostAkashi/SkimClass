import os
import time
import base64
import json
import requests
import urllib3
from openai import OpenAI
# 1. 保持暴力联网 (为了发图片)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 🔑 密钥配置 (保持不变) =================
ASR_APPID = "YOUR_APP_ID_HERE"        
ASR_TOKEN = "YOUR_TOKEN_HERE"
ASR_CLUSTER = "OUR_CLUSTER_ID_HERE"

LLM_API_KEY = "YOUR_API_KEY_HERE"
LLM_ENDPOINT = "YOUR_ENDPOINT_ID_HERE"
# ==========================================================

class DoubaoClient:
    def __init__(self):
        self.llm_client = OpenAI(
            api_key=LLM_API_KEY,
            base_url="https://ark.cn-beijing.volces.com/api/v3",
        )

    def audio_to_text(self, file_path):
        # 系统“默认”进入纯视觉模式。
        print("⚠️ 进入视觉分析模式...")
        return "" 

    def generate_report(self, text_context, image_paths=[]):
        print("🧠 正在根据《立项报告》标准生成含习题的报告...")
        
        system_prompt = """
        你是由哈尔滨工业大学学生开发的“基于大模型智能体的助教系统”。
        你的任务是根据课堂截图，生成一份符合《立项报告》标准的结构化学习报告。
        
        🚨 **视觉抗干扰指令**：
        - 严禁分析包含 "Streamlit", "Deploy", "助教控制台" 字样的界面截图。
        - 严禁分析代码编辑器（VS Code）的界面。
        - **只分析**展示了课程知识点（PPT、PDF、板书）的图片。
        
        📝 **报告生成标准 (必须严格遵守)**：
        
        **第一部分：知识结构图谱 (对应立项 2.4.3)**
        - 请用 Markdown 缩进列表形式，梳理本节课的知识层级结构。
        
        **第二部分：核心内容详解 (对应立项 2.4.4 "有据可查")**
        - 详细讲解 3-5 个核心知识点。
        - **关键要求**：每讲解一个点，必须标注来源证据！例如：“...根据[图3]所示公式...”。
        
        **第三部分：边学边测 (对应立项 2.4.5)**
        - 这是用户最看重的功能。请必须根据本节课内容，出 **3 道单项选择题**。
        - 格式必须如下：
          **Q1. [题目内容]**
          A. [选项]  B. [选项]  C. [选项]  D. [选项]
          > ✅ **正确答案**：X
          > 💡 **解析**：[一句话解析]
          
        **第四部分：待复习薄弱点**
        - 预测学生可能听不懂的 1-2 个难点，建议复习方向。
        """

        content_payload = []
        # 即使文字为空，我们也要强行让 AI 看图
        content_payload.append({"type": "text", "text": "【课堂录音】：(无，请全权基于截图分析)\n【课堂截图序列】："})

        # 筛选图片：为了让习题更准，我们稍微多取几张
        selected = image_paths[::2][:8] 
        valid_count = 0
        for i, p in enumerate(selected):
            if os.path.exists(p):
                b64 = self._encode_image(p)
                # 给图片编号，方便 AI 引用
                content_payload.append({"type": "text", "text": f"[图{i+1}]"})
                content_payload.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                valid_count += 1
        
        if valid_count == 0:
            return "❌ 未检测到有效截图。请在上课时确保网课窗口在最前端。"

        try:
            res = self.llm_client.chat.completions.create(
                model=LLM_ENDPOINT,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": content_payload}]
            )
            return res.choices[0].message.content
        except Exception as e:
            return f"❌ 报告生成失败: {e}"

    def _encode_image(self, image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def chat_with_context(self, question, context_text, image_paths):
        # 问答模式也对齐
        system_prompt = """
        你是智能助教。请忽略助教控制台截图。
        回答问题时，请尽量引用截图作为证据（例如：“你可以看[图2]...”）。
        """
        content_payload = []
        content_payload.append({"type": "text", "text": f"问题：{question}\n截图证据："})
        
        selected = image_paths[::2][:6]
        for i, p in enumerate(selected):
            if os.path.exists(p):
                b64 = self._encode_image(p)
                content_payload.append({"type": "text", "text": f"[图{i+1}]"})
                content_payload.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

        try:
            res = self.llm_client.chat.completions.create(
                model=LLM_ENDPOINT,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": content_payload}]
            )
            return res.choices[0].message.content
        except Exception as e:
            return f"❌ 回答失败: {e}"