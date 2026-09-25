"""Đọc cấu hình từ config.yaml, gộp với giá trị mặc định."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = ROOT / "config.example.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config(dict):
    """dict có thể truy cập kiểu cfg.get_path("llm.model")."""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict:
        return self.get(name) or {}

    def resolve(self, path: str | os.PathLike) -> Path:
        """Đường dẫn tương đối được tính từ thư mục gốc dự án."""
        p = Path(path).expanduser()
        return p if p.is_absolute() else ROOT / p


def load_config(path: str | os.PathLike | None = None) -> Config:
    defaults = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8")) or {}
    candidates = [path] if path else [os.environ.get("AUTOTUBE_CONFIG"), ROOT / "config.yaml"]
    user_cfg: dict = {}
    for cand in candidates:
        if cand and Path(cand).exists():
            user_cfg = yaml.safe_load(Path(cand).read_text(encoding="utf-8")) or {}
            break
    cfg = Config(_deep_merge(defaults, user_cfg))

    # Cho phép ghi đè bí mật bằng biến môi trường
    env_map = {
        "PEXELS_API_KEY": ("media", "pexels_api_key"),
        "PIXABAY_API_KEY": ("media", "pixabay_api_key"),
        "AUTOTUBE_LLM_MODEL": ("llm", "model"),
        "AUTOTUBE_LLM_URL": ("llm", "base_url"),
        "AUTOTUBE_LLM_API_KEY": ("llm", "api_key"),
    }
    for env, (sec, key) in env_map.items():
        if os.environ.get(env):
            cfg.setdefault(sec, {})[key] = os.environ[env]
    return cfg
