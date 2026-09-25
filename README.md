# 🎬 AutoTube — AI local tự làm video và tự đăng YouTube

AI chạy **ngay trên máy của bạn** (Ollama / LM Studio...) tự nghĩ ý tưởng, viết kịch bản, tìm hình ảnh,
lồng tiếng, làm phụ đề, cắt ghép, thêm nhạc, tạo thumbnail, viết SEO rồi **tự đăng lên YouTube**.
Ở chế độ **Agent**, AI được giao một bộ ~35 công cụ dựng phim và **tự quyết định** dùng công cụ nào,
theo thứ tự nào, sửa đi sửa lại tới khi thấy video đạt rồi mới đăng.

---

## 1. Kế hoạch & kiến trúc

```
┌──────────────────────────── MÁY CỦA BẠN ──────────────────────────────┐
│                                                                       │
│   ┌──────────────┐   tool-calling   ┌──────────────────────────────┐  │
│   │  AI LOCAL     │ ◄──────────────► │  AGENT (autotube/agent)      │  │
│   │  Ollama /     │                  │  tự lên kế hoạch, gọi công cụ│  │
│   │  LM Studio... │                  └──────────────┬───────────────┘  │
│   └──────────────┘                                  │                  │
│                                     ┌───────────────▼───────────────┐  │
│                                     │  STUDIO (autotube/studio.py)   │  │
│                                     │  bộ tính năng sản xuất video   │  │
│                                     └───────────────┬───────────────┘  │
│  ┌────────────┬────────────┬────────────┬───────────┼──────┬────────┐  │
│  ▼            ▼            ▼            ▼           ▼      ▼        ▼  │
│ Ý tưởng &   Hình ảnh    Lồng tiếng   Phụ đề     Dựng video  Thumb  YouTube
│ kịch bản    Pexels,     Edge TTS /   SRT + ASS  FFmpeg:     nail   API v3
│ (LLM)       Pixabay,    Piper        karaoke,   cắt, ghép,  + SEO  upload,
│ Google      local, SD   (offline)    Whisper    chuyển cảnh,       lịch,
│ Trends                                          zoom, nhạc         phụ đề
└───────────────────────────────────────────────────────────────────────┘
```

### Các bước AI thực hiện cho mỗi video

| # | Bước | Công cụ / thư viện |
|---|------|--------------------|
| 1 | Nghĩ ý tưởng (tham khảo Google Trends, tránh trùng chủ đề cũ) | LLM local + RSS Google Trends |
| 2 | Viết kịch bản theo cảnh (hook 5s đầu, lời thoại, từ khoá hình ảnh, chữ trên màn hình, hiệu ứng, chuyển cảnh) và tự phê bình để viết lại hay hơn | LLM local |
| 3 | Tìm & tải video/ảnh minh hoạ cho từng cảnh | Thư mục của bạn → Pexels → Pixabay → Stable Diffusion local → nền tự tạo |
| 4 | Lồng tiếng từng cảnh, lấy mốc thời gian từng từ | Edge TTS (miễn phí) hoặc Piper (offline) |
| 5 | Chọn nhạc nền theo tâm trạng (hoặc tự tổng hợp) | Thư viện nhạc local |
| 6 | Tạo phụ đề SRT + ASS có hiệu ứng karaoke | Mốc thời gian TTS hoặc faster-whisper |
| 7 | Dựng video: cắt/lặp clip đúng độ dài lời thoại, zoom Ken Burns cho ảnh, chữ trên màn hình, 20 kiểu chuyển cảnh, trộn nhạc tự giảm âm khi có lời (ducking), chuẩn hoá âm lượng −14 LUFS, in phụ đề | FFmpeg |
| 8 | Tạo thumbnail chữ lớn, viết tiêu đề/mô tả/tags/hashtag | Pillow + LLM |
| 9 | Tự kiểm tra chất lượng (`review_project`), sửa lỗi, render lại nếu cần | Agent |
| 10 | Đăng YouTube (riêng tư/không công khai/công khai, hẹn giờ), đặt thumbnail, upload phụ đề | YouTube Data API v3 |
| 11 | Lặp lại theo lịch (vd. mỗi ngày 1 video) | `schedule` |

### Hai chế độ chạy

- **`agent`** (mặc định) — AI tự làm chủ: tự chọn chủ đề, tự viết kịch bản (hoặc nhờ công cụ viết), tự thay
  hình cảnh nào xấu, tự chọn giọng/nhạc/kiểu phụ đề, tự rà soát rồi đăng. Cần model hỗ trợ tool-calling
  (khuyên dùng `qwen2.5:7b` trở lên). Model không có tool-calling vẫn chạy được nhờ giao thức JSON dự phòng.
  Nếu agent bỏ sót bước nào, chương trình tự làm nốt (`agent.ensure_complete`).
- **`pipeline`** — quy trình cố định, AI chỉ lo phần sáng tạo nội dung. Ổn định nhất với model nhỏ (3B–7B).

---

## 2. Cấu trúc thư mục

```
bot-Youtube/
├── autotube/
│   ├── cli.py              # lệnh: init, doctor, auth, ideas, make, edit, resume, upload, schedule, voices
│   ├── config.py           # đọc config.yaml
│   ├── studio.py           # ★ tập hợp mọi tính năng sản xuất video
│   ├── pipeline.py         # chế độ quy trình cố định
│   ├── project.py          # trạng thái video (output/<id>/project.json)
│   ├── agent/
│   │   ├── agent.py        # ★ vòng lặp AI tự quyết định (native tool-calling + JSON fallback)
│   │   └── tools.py        # ★ ~35 công cụ AI được dùng
│   ├── llm/client.py       # kết nối Ollama & server chuẩn OpenAI (LM Studio, llama.cpp, vLLM...)
│   ├── content/generator.py# ý tưởng, kịch bản, tự phê bình, SEO, Google Trends, lịch sử chủ đề
│   ├── media/sources.py    # local / Pexels / Pixabay / Stable Diffusion / nền tự tạo
│   ├── audio/tts.py        # Edge TTS, Piper, dummy
│   ├── audio/music.py      # chọn nhạc theo mood, tổng hợp nhạc ambient
│   ├── subtitles/subs.py   # SRT, ASS karaoke, Whisper, in phụ đề dự phòng bằng Pillow
│   ├── video/editor.py     # ★ FFmpeg: clip cảnh, xfade, trộn âm, render, cắt, ghép, tốc độ, chữ, đổi khung
│   ├── video/thumbnail.py  # thumbnail
│   └── youtube/uploader.py # OAuth + upload + thumbnail + phụ đề + hẹn giờ
├── scripts/download_tools.py  # tải FFmpeg, Piper, font, yt-dlp từ GitHub
├── assets/fonts/           # font Be Vietnam Pro (hỗ trợ tiếng Việt, giấy phép OFL)
├── assets/music/           # ← bỏ nhạc nền không bản quyền vào đây
├── assets/media/           # ← (tuỳ chọn) ảnh/video của bạn
├── config.example.yaml     # cấu hình mẫu (có chú thích tiếng Việt)
├── install.bat / run.bat   # Windows
├── install.sh  / run.sh    # Linux / macOS
└── tests/                  # test tự động (chạy không cần AI/GPU)
```

---

## 3. Cài đặt

### Yêu cầu máy

| Thành phần | Tối thiểu | Khuyên dùng |
|-----------|-----------|-------------|
| RAM | 8 GB (model 3B) | 16 GB+ |
| GPU | không bắt buộc (chạy CPU chậm hơn) | NVIDIA 8 GB VRAM+ |
| Ổ cứng | 10 GB trống | SSD |
| Python | 3.10+ | 3.11 |

### Chọn model AI local

| Model (Ollama) | Dung lượng | Ghi chú |
|---------------|-----------|---------|
| `qwen2.5:3b` | ~2 GB | máy yếu, dùng `--mode pipeline` |
| `qwen2.5:7b` | ~4.7 GB | **khuyên dùng** — tiếng Việt tốt, tool-calling tốt |
| `qwen2.5:14b` | ~9 GB | chất lượng kịch bản tốt hơn |
| `llama3.1:8b`, `mistral-nemo` | 5–7 GB | tool-calling tốt, tiếng Việt kém hơn Qwen |
| `qwen3:8b` / `gemma3` ... | | dùng được; phần `<think>` được tự lọc bỏ |

### Windows

1. Cài [Python 3.10+](https://www.python.org/downloads/) (tick **Add Python to PATH**).
2. Tải dự án về, nhấp đúp **`install.bat`**. Script sẽ:
   tạo `.venv` → cài thư viện → tải FFmpeg + font từ GitHub → tạo `config.yaml` → cài Ollama (winget) → `ollama pull qwen2.5:7b`.

### Linux / macOS

```bash
git clone https://github.com/Vecter-Core/bot-Youtube.git && cd bot-Youtube
./install.sh
```

### Cài thủ công

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/download_tools.py        # FFmpeg + font (thêm --piper để có giọng đọc offline)
python -m autotube init                 # tạo config.yaml
# Cài Ollama: https://ollama.com/download   rồi:
ollama pull qwen2.5:7b
```

### Công cụ mã nguồn mở được dùng (tải từ GitHub/PyPI)

| Công cụ | Dùng để | Nguồn |
|--------|---------|-------|
| Ollama | chạy AI local | github.com/ollama/ollama |
| FFmpeg | cắt ghép, hiệu ứng, trộn âm, phụ đề | github.com/BtbN/FFmpeg-Builds (hoặc `imageio-ffmpeg`) |
| edge-tts | lồng tiếng giọng Việt tự nhiên | github.com/rany2/edge-tts |
| Piper | lồng tiếng offline | github.com/rhasspy/piper (+ giọng `vi_VN-vais1000-medium`) |
| faster-whisper | căn phụ đề theo giọng nói (tuỳ chọn) | github.com/SYSTRAN/faster-whisper |
| Pillow | chữ, thumbnail, nền | github.com/python-pillow/Pillow |
| google-api-python-client | YouTube API | github.com/googleapis/google-api-python-client |
| Be Vietnam Pro | font tiếng Việt | github.com/google/fonts |
| yt-dlp (tuỳ chọn) | tải video **bạn có quyền dùng** | github.com/yt-dlp/yt-dlp |
| Stable Diffusion WebUI (tuỳ chọn) | AI vẽ ảnh minh hoạ local | github.com/AUTOMATIC1111/stable-diffusion-webui |

---

## 4. Cấu hình

Mở `config.yaml` (có chú thích từng dòng). Quan trọng nhất:

```yaml
channel:
  niche: "Khám phá khoa học và những sự thật thú vị"   # chủ đề kênh — AI dựa vào đây
  language: "vi"
llm:
  provider: "ollama"            # hoặc openai_compat cho LM Studio (base_url: http://localhost:1234/v1)
  model: "qwen2.5:7b"
video:
  format: "landscape"           # hoặc shorts (9:16)
  target_duration: 60
media:
  pexels_api_key: "..."         # miễn phí tại https://www.pexels.com/api/ — rất nên có
youtube:
  privacy: "private"            # nên để private lúc đầu để kiểm tra
```

### Kết nối YouTube (làm 1 lần)

1. Vào [Google Cloud Console](https://console.cloud.google.com/) → tạo project mới.
2. **APIs & Services → Library** → bật **YouTube Data API v3**.
3. **OAuth consent screen** → External → điền tên app, email → thêm email của bạn vào **Test users**.
4. **Credentials → Create credentials → OAuth client ID → Desktop app** → tải JSON về,
   đổi tên thành `credentials/client_secret.json`.
5. Chạy `run.bat auth` (Windows) hoặc `./run.sh auth` → trình duyệt mở ra → đăng nhập kênh YouTube → cho phép.

> ⚠️ App Google ở chế độ "Testing" chưa qua kiểm duyệt: YouTube có thể **khoá video ở chế độ riêng tư**.
> Muốn đăng công khai tự động, hãy gửi yêu cầu [YouTube API Audit](https://support.google.com/youtube/contact/yt_api_form).
> Hạn mức mặc định 10.000 đơn vị/ngày; mỗi lần upload tốn ~1.600 → tối đa ~6 video/ngày.
> Kênh cần xác minh số điện thoại để đặt thumbnail tuỳ chỉnh.

---

## 5. Sử dụng

(Windows dùng `run.bat <lệnh>`, Linux/macOS dùng `./run.sh <lệnh>` — tương đương `python -m autotube <lệnh>`.)

```bash
run.bat doctor                          # kiểm tra mọi thứ đã sẵn sàng chưa
run.bat ideas                           # AI gợi ý 8 ý tưởng video

run.bat make                            # ★ AI tự làm từ A-Z và đăng YouTube
run.bat make --no-upload                # chỉ làm video, chưa đăng (xem trong output/)
run.bat make --topic "Vì sao bầu trời màu xanh"
run.bat make --format shorts --duration 45
run.bat make --mode pipeline            # quy trình cố định (máy yếu / model nhỏ)
run.bat make --hint "series về vũ trụ, giọng kể bí ẩn"

run.bat edit output/2026...-ten-video "Đổi giọng nam, cảnh 2 dùng video biển, thêm chữ 'Bất ngờ!' ở cảnh cuối"
run.bat resume output/2026...-ten-video --upload        # làm nốt / đăng project dang dở
run.bat upload output/2026...-ten-video --privacy public --publish-at 2026-10-01T12:00:00Z

run.bat schedule                        # chạy mãi: mỗi 24 giờ tự làm & đăng 1 video
run.bat schedule --interval-hours 12 --count 2 --format shorts

run.bat voices --lang vi                # liệt kê giọng đọc tiếng Việt
```

Kết quả mỗi video nằm trong `output/<thời-gian>-<chủ-đề>/`: `final.mp4`, `thumbnail.jpg`, `subtitles.srt`,
`project.json` (kịch bản + trạng thái), các clip trung gian. Log đầy đủ ở `output/autotube.log`.

### Tự chạy khi bật máy

- **Windows**: Task Scheduler → Create Basic Task → Action: *Start a program* →
  `C:\đường-dẫn\bot-Youtube\run.bat`, Arguments: `schedule --once` → Trigger: Daily.
- **Linux**: `crontab -e` → `0 8 * * * /đường-dẫn/bot-Youtube/run.sh schedule --once >> /tmp/autotube.log 2>&1`

---

## 6. Bộ công cụ của Agent

| Nhóm | Công cụ |
|------|---------|
| Ý tưởng & kịch bản | `get_trending_topics`, `brainstorm_ideas`, `write_script`, `set_script` (AI tự viết), `view_script`, `improve_script`, `edit_scene`, `add_scene`, `remove_scene`, `move_scene` |
| Hình ảnh | `find_visuals_for_all_scenes`, `search_media`, `set_scene_media`, `generate_image` (Stable Diffusion) |
| Âm thanh | `generate_voiceover`, `list_voices`, `choose_background_music` |
| Dựng & xuất bản | `generate_subtitles`, `render_video`, `create_thumbnail`, `generate_seo`, `set_metadata`, `review_project`, `upload_to_youtube`, `finish` |
| Chỉnh sửa tự do | `trim_video`, `join_videos`, `change_speed`, `add_text_to_video`, `replace_audio`, `convert_format`, `make_shorts_version`, `transcribe_media`, `inspect_media`, `list_project_files` |

Muốn thêm tính năng mới cho AI: viết hàm trong `studio.py`/`video/editor.py`, rồi đăng ký một dòng `add(...)`
trong `agent/tools.py` — AI sẽ tự đọc mô tả và biết dùng.

---

## 7. Mẹo chất lượng

- **Có Pexels API key** → video thật thay cho nền màu. Thêm Pixabay để nhiều lựa chọn hơn.
- Bỏ **nhạc không bản quyền** vào `assets/music/` và đặt tên theo mood (`calm_...`, `epic_...`).
- Giọng tiếng Việt Edge: `vi-VN-HoaiMyNeural` (nữ), `vi-VN-NamMinhNeural` (nam).
- Offline hoàn toàn: `python scripts/download_tools.py --piper` rồi đặt `tts.engine: piper`.
- Phụ đề khớp hơn khi dùng Piper: `pip install faster-whisper` và `subtitles.use_whisper: true`.
- Để `youtube.privacy: private` vài video đầu, xem lại rồi mới chuyển sang `public`.

## 8. Xử lý sự cố

| Lỗi | Cách xử lý |
|-----|-----------|
| `Không kết nối được AI local` | Mở Ollama (hoặc `ollama serve`), kiểm tra `llm.base_url` |
| `model ... not found` | `ollama pull qwen2.5:7b` hoặc sửa `llm.model` |
| `does not support tools` | Tự chuyển sang giao thức JSON; hoặc đặt `llm.native_tools: false`; hoặc dùng `--mode pipeline` |
| Edge TTS lỗi mạng | Thử lại, hoặc dùng `--tts piper` |
| Video toàn nền màu | Chưa có Pexels/Pixabay key và thư mục `assets/media` trống |
| `Thiếu client_secret.json` | Làm mục "Kết nối YouTube" ở trên |
| Video bị khoá riêng tư | App Google chưa qua kiểm duyệt API — xem ghi chú mục 4 |

## 9. Lưu ý pháp lý & chính sách

- Chỉ dùng hình ảnh/nhạc bạn có quyền (Pexels/Pixabay cho phép dùng miễn phí; nhạc từ YouTube Audio Library).
  **Không** dùng yt-dlp để tải và đăng lại video của người khác.
- YouTube yêu cầu nội dung có giá trị, không spam hàng loạt. Nên xem lại video trước khi công khai.
- Nếu video có người/sự kiện trông như thật do AI tạo, bật `youtube.contains_synthetic_media: true`.

## 10. Phát triển

```bash
pip install -r requirements-dev.txt
python -m pytest -q        # chạy toàn bộ test (dựng video thật bằng FFmpeg, AI giả lập — không cần GPU)
```
