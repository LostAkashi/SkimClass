#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "首次运行：正在创建虚拟环境并安装依赖，这可能需要几分钟……"
  python3 -m venv .venv || exit 1
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r backend/requirements.txt
else
  source .venv/bin/activate
fi

# 进入 backend 目录，这样才能找到 skimclass 包
cd backend

python3 -m webbrowser http://127.0.0.1:7860 >/dev/null 2>&1 &

python3 -m uvicorn skimclass.web:app --host 127.0.0.1 --port 7860
