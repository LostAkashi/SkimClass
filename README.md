
# 略课 SkimClass（一键本地版）

这是一个尽量「开箱即用」的课堂智能助教原型：

- 在本地后台自动截取屏幕，跟随你上网课 / 线下上课的投屏画面；
- 下课后一键生成本节课的 Outline（分小节总结 + 学生可能的困惑）；
- 基于课堂内容回答问题、生成课后小测、学习报告以及拓展学习建议；
- 所有数据都存放在本机 `backend/data/` 目录，不上传到云端。

## 一、使用前提

- 电脑已安装 **Python 3.10+**；
- 系统：Windows 或 macOS（Linux 也可以，但需要自己在终端运行 uvicorn）。

## 二、一键启动

1. 解压本项目压缩包，例如解压到 `~/SkimClass_OneClick`；
2. **macOS**：
   - 右键 `start_mac.command` → 打开方式选择「终端」；
   - 如果提示权限问题，可以在终端执行一次：

     ```bash
     chmod +x start_mac.command
     ```

     之后直接双击即可。
3. **Windows**：
   - 双击 `start_windows.bat`；

首次运行时，脚本会自动：

- 创建本地虚拟环境 `.venv`；
- 安装 `backend/requirements.txt` 中的依赖；
- 启动后端服务，并自动打开浏览器访问：`http://127.0.0.1:7860`。

以后再次双击脚本就会直接启动，不需要重新安装依赖。

## 三、首次配置大模型

打开页面右上角的「设置大模型」，填写：

- **API Base**：例如 `https://api.deepseek.com`、豆包的 OpenAI 协议地址等；
- **Model**：例如 `deepseek-chat`；
- **API Key**：从对应平台复制。

保存后即可开始使用。

## 四、课堂使用流程

1. 上课前：
   - 在页面左侧「开始一节课」中填写课程名称、选择采集模式；
   - 点击 **Start Session**，系统开始后台截屏（轻量模式除外）。

2. 下课后：
   - 点击 **Stop** 停止采集；
   - 点击 **生成 Outline** 提交总结任务；
   - 等一两分钟后，点击右上角 **刷新** 查看 Outline。

3. 之后你可以：
   - 在「提问 / 解惑」中就本节课提问；
   - 使用「课后小测」生成几道选择题自测；
   - 在「拓展学习建议」里获取延伸阅读与预习建议；
   - 在「学习报告」中生成一份可复制到笔记里的复习报告。

## 五、目录结构简要说明

```text
SkimClass_OneClick/
  start_mac.command       # macOS 启动脚本
  start_windows.bat       # Windows 启动脚本
  backend/
    requirements.txt
    skimclass/
      config.py           # 大模型配置与本地 data 目录
      db.py               # SQLite 存储（session / 截图索引 / 小节 / 小测）
      capture.py          # 后台截屏线程
      llm_client.py       # 调用 OpenAI 协议兼容接口
      summarization.py    # 生成 Outline / 问答 / 小测 / 报告 / 拓展建议
      web.py              # FastAPI 应用 + 静态页面
      static/
        index.html        # 前端页面
        style.css         # 简单样式
        app.js            # 页面逻辑（通过 fetch 调用后端）
```

在这个基础上，你可以继续拓展：

- 加入音频录制 + 语音转写，实现「画面 + 语音」联合总结；
- 接入教材 PDF / 课件 PPT 做真正的知识库检索；
- 做多课程管理、成绩追踪、错题本等更完整的学习分析功能。
