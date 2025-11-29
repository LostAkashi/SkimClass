@echo off
cd /d %~dp0

if not exist .venv (
  echo 首次运行：正在创建虚拟环境并安装依赖，这可能需要几分钟……
  py -m venv .venv
  call .venv\Scripts\activate
  pip install --upgrade pip
  pip install -r backend\requirements.txt
) else (
  call .venv\Scripts\activate
)

cd backend
start "" "http://127.0.0.1:7860"
python -m uvicorn skimclass.web:app --host 127.0.0.1 --port 7860
