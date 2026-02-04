# 🎓 AI-Tutor-Agent: 基于大模型智能体的助教系统

> **哈尔滨工业大学 - 大一年度项目立项作品** > 打造“上课能用、课后好复习”的个性化 AI 伴学助手。

## 📖 项目介绍 (Introduction)

本项目是一个面向大学课堂与在线课程的智能助教 Agent。它通过**多模态感知技术**（屏幕视觉 + 音频听觉），实时“旁听”用户的网课或线下课程，并利用 **Doubao-1.5-Vision** 等大模型能力，自动生成结构化的学习报告、知识图谱及课后习题。

与传统的“死板”录屏软件不同，本系统具备**视觉认知能力**，能够像人类助教一样理解 PPT 内容、忽略无关干扰界面，并根据立项报告中的教学理论提供“边学边测”的反馈机制。

## ✨ 核心功能 (Features)

* **👁️ 智能视觉感知**：
    * 自动截取课程 PPT 关键帧（基于图像哈希去重）。
    * **视觉防火墙**：内置 Prompt 级抗干扰机制，自动过滤系统控制台、IDE 等非课程画面，专注课程内容。
* **🧠 结构化笔记生成**：
    * 生成 Markdown 格式的**知识结构图谱**。
    * **有据可查**：核心知识点讲解自动标注来源截图（如“参考 [图3]”）。
* **📝 自动化测验 (Quiz)**：
    * 根据本节课内容，自动生成 3 道单项选择题，并附带正确答案与详细解析，实现“边学边测”。
* **💬 上下文感知问答**：
    * 课后答疑模式，AI 助教基于本节课的视觉记忆回答用户提问。

## 🛠️ 技术栈 (Tech Stack)

* **前端交互**: [Streamlit](https://streamlit.io/)
* **端侧采集**: `mss` (屏幕录制), `pyaudio` (音频流处理)
* **大模型基座**: 火山引擎 (Volcengine) Doubao-1.5-Vision-Pro
* **图像处理**: Pillow, ImageHash

🖥️ 使用指南
1.启动采集：点击左侧侧边栏的 “🚀 开始上课” 按钮。
2.专注听课：请务必将网课视频窗口置于屏幕最前方（或全屏播放），系统会自动捕获 PPT 内容。
3.生成报告：课程结束后点击 “⏹ 下课”，系统将自动上传数据并生成包含习题的学习报告。

## 🚀 快速开始 (Quick Start)
### 1. 环境准备
确保你的电脑已安装 Python 3.8+。

```bash
# 克隆项目
git clone https://github.com/LostAkashi/SkimClass.git
cd SkimClass

# 安装依赖
pip install -r requirements.txt

2. 配置 API 密钥
本项目依赖火山引擎（Volcengine）的大模型服务。 请打开 doubao_api.py，填入你自己的密钥：
ASR_APPID = "你的_APPID"
ASR_TOKEN = "你的_TOKEN"
LLM_API_KEY = "你的_API_KEY"
LLM_ENDPOINT = "你的_ENDPOINT_ID"

3. 运行系统
在终端中执行：
streamlit run app.py