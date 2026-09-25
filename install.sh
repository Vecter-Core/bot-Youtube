#!/usr/bin/env bash
# Cài đặt AutoTube trên Linux / macOS
set -e
cd "$(dirname "$0")"
echo "=== CÀI ĐẶT AUTOTUBE ==="

PY=${PYTHON:-python3}
command -v $PY >/dev/null || { echo "Chưa có Python 3.10+"; exit 1; }

[ -d .venv ] || { echo "[1/5] Tạo môi trường ảo .venv"; $PY -m venv .venv; }
source .venv/bin/activate

echo "[2/5] Cài thư viện Python"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "[3/5] Tải FFmpeg + font từ GitHub"
python scripts/download_tools.py || echo "(Có thể cài FFmpeg bằng: sudo apt install ffmpeg / brew install ffmpeg)"

echo "[4/5] Tạo file cấu hình"
python -m autotube init

echo "[5/5] Kiểm tra AI local (Ollama)"
if ! command -v ollama >/dev/null; then
  read -r -p "Chưa có Ollama. Cài ngay bằng script chính thức (https://ollama.com/install.sh)? [y/N] " ans
  if [[ "$ans" =~ ^[Yy]$ ]]; then
    if [[ "$(uname)" == "Darwin" ]]; then
      echo "macOS: tải Ollama tại https://ollama.com/download hoặc: brew install ollama"
    else
      curl -fsSL https://ollama.com/install.sh | sh
    fi
  fi
fi
if command -v ollama >/dev/null; then
  ollama pull qwen2.5:7b || echo "Hãy chạy 'ollama serve' rồi 'ollama pull qwen2.5:7b'"
fi

echo
echo "XONG! Tiếp theo:"
echo "  1. Sửa config.yaml (chủ đề kênh, API key Pexels...)"
echo "  2. Đặt client_secret.json vào credentials/  (xem README)"
echo "  3. ./run.sh auth     # đăng nhập YouTube"
echo "  4. ./run.sh doctor   # kiểm tra"
echo "  5. ./run.sh make     # AI tự làm và đăng 1 video"
