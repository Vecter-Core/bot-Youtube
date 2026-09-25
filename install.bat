@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ================================================
echo   CAI DAT AUTOTUBE - AI tu lam video YouTube
echo ================================================

where python >nul 2>nul
if errorlevel 1 (
  echo [LOI] Chua co Python. Tai Python 3.10+ tai https://www.python.org/downloads/
  echo       Nho tick "Add Python to PATH" khi cai.
  pause & exit /b 1
)

if not exist .venv (
  echo [1/5] Tao moi truong ao .venv ...
  python -m venv .venv || (echo [LOI] Khong tao duoc venv & pause & exit /b 1)
)
call .venv\Scripts\activate.bat

echo [2/5] Cai thu vien Python ...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || (echo [LOI] pip install that bai & pause & exit /b 1)

echo [3/5] Tai FFmpeg + font tu GitHub ...
python scripts\download_tools.py

echo [4/5] Tao file cau hinh ...
python -m autotube init

echo [5/5] Kiem tra AI local (Ollama) ...
where ollama >nul 2>nul
if errorlevel 1 (
  echo Chua co Ollama. Dang thu cai bang winget ...
  winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
  if errorlevel 1 (
    echo Hay tai Ollama thu cong: https://ollama.com/download  ^(hoac https://github.com/ollama/ollama/releases^)
  )
)
where ollama >nul 2>nul
if not errorlevel 1 (
  echo Tai model qwen2.5:7b ^(~4.7GB^) ...
  ollama pull qwen2.5:7b
)

echo.
echo XONG! Buoc tiep theo:
echo   1. Mo config.yaml de chinh chu de kenh, API key Pexels...
echo   2. Dat file client_secret.json vao thu muc credentials\  ^(xem README^)
echo   3. run.bat auth      ^(dang nhap YouTube 1 lan^)
echo   4. run.bat doctor    ^(kiem tra^)
echo   5. run.bat make      ^(AI tu lam va dang 1 video^)
pause
