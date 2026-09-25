import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture
def cfg(tmp_path):
    from autotube.config import load_config

    c = load_config(tmp_path / "none.yaml")
    c["paths"] = {"output_dir": str(tmp_path / "out"), "history_file": str(tmp_path / "out" / "history.json")}
    c["video"].update({"format": "720p", "preset": "ultrafast", "crf": 30, "fps": 24, "target_duration": 15})
    c["tts"]["engine"] = "dummy"
    c["media"].update({"sources": ["local", "placeholder"], "local_dir": str(tmp_path / "media")})
    c["music"]["dir"] = str(tmp_path / "music")
    c["channel"]["use_trends"] = False
    return c
