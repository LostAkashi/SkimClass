import streamlit as st
import os
import time
from auto_capture import AutoRecorder
from doubao_api import DoubaoClient

st.set_page_config(page_title="AI 智能助教", layout="wide", page_icon="🎓")
# 初始化 Session State
if "recorder" not in st.session_state:
    st.session_state.recorder = None
if "is_recording" not in st.session_state:
    st.session_state.is_recording = False
if "course_data" not in st.session_state:
    st.session_state.course_data = None # 用来存这节课的记忆 (文字+图片路径)
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# ================= 侧边栏 =================
with st.sidebar:
    st.title("🎓 助教控制台")
    course_name = st.text_input("📝 课程名称", value="计算机组成原理")
    
    st.divider()
    
    # 录制控制区
    if not st.session_state.is_recording:
        if st.button("🚀 开始上课 (录制)", type="primary", use_container_width=True):
            st.session_state.recorder = AutoRecorder(course_name)
            st.session_state.recorder.start()
            st.session_state.is_recording = True
            st.rerun()
    else:
        st.error(f"🎙️ 正在听课中... ({course_name})")
        st.info("💡 请将网课窗口置于最前！")
        if st.button("⏹ 下课 (生成笔记)", type="primary", use_container_width=True):
            with st.spinner("💾 正在整理记忆..."):
                st.session_state.recorder.stop()
                save_dir = st.session_state.recorder.save_dir
                
                # 自动处理
                client = DoubaoClient()
                audio_path = os.path.join(save_dir, "lecture.wav")
                screenshot_dir = os.path.join(save_dir, "screenshots")
                
                # 1. 听录音
                text = client.audio_to_text(audio_path)
                
                # 2. 找图片
                imgs = []
                if os.path.exists(screenshot_dir):
                    imgs = [os.path.join(screenshot_dir, f) for f in os.listdir(screenshot_dir) if f.endswith(".jpg")]
                    imgs.sort()
                
                # 3. 存入记忆，供后续问答使用
                st.session_state.course_data = {
                    "text": text,
                    "images": imgs,
                    "dir": save_dir
                }
                
                # 4. 生成初始报告
                report = client.generate_report(text, imgs)
                # 把报告作为第一条对话
                st.session_state.chat_history = [
                    {"role": "assistant", "content": f"## ✅ {course_name} 课程报告\n\n{report}"}
                ]
                
            st.session_state.is_recording = False
            st.rerun()

    st.divider()
    if st.session_state.course_data:
        st.success("🧠 助教已加载本节课记忆")
    else:
        st.caption("💤 助教暂无课程记忆")

# ================= 主界面 (多标签页) =================
st.title(f"🤖 AI 助教：{course_name}")

# 如果还没上课，显示欢迎页
if not st.session_state.course_data and not st.session_state.is_recording:
    st.info("👋 欢迎！请点击左侧 **“开始上课”**，我会陪你一起听课、记笔记。")
    st.markdown("---")
    st.markdown("### 🌟 我能做什么？")
    col1, col2, col3 = st.columns(3)
    col1.metric("👂 全程听写", "语音转文字")
    col2.metric("📸 智能抓拍", "PPT关键帧")
    col3.metric("💬 课后答疑", "基于上下文")

# 如果已经有数据了，显示聊天界面
else:
    # 显示聊天记录
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 聊天输入框
    if prompt := st.chat_input("对这节课有什么不懂的？问我吧！"):
        # 1. 显示用户提问
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # 2. 助教思考并回答
        if st.session_state.course_data:
            with st.chat_message("assistant"):
                with st.spinner("🤔 回忆课程内容中..."):
                    client = DoubaoClient()
                    # 调用刚才新写的 chat_with_context
                    response = client.chat_with_context(
                        question=prompt,
                        context_text=st.session_state.course_data["text"],
                        image_paths=st.session_state.course_data["images"]
                    )
                    st.markdown(response)
                    st.session_state.chat_history.append({"role": "assistant", "content": response})
        else:
            st.error("❌ 尚未生成课程数据，无法回答。")