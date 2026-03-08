@echo off
setlocal
cd /d %~dp0

py -m pip install --upgrade pip
py -m pip install pyinstaller

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

pyinstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --name SkimClass ^
  --recursive-copy-metadata streamlit ^
  launcher.py ^
  --add-data "app.py;." ^
  --add-data "agent.py;." ^
  --add-data "auto_capture.py;." ^
  --add-data "db.py;." ^
  --add-data "paths.py;." ^
  --add-data "pages;pages" ^
  --add-data "data\recordings;data\recordings" ^
  --add-data ".env.example;."

echo Done: dist\SkimClass\SkimClass.exe
endlocal
