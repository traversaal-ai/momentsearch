"""Central configuration, loaded from environment / `.env`.

Every knob has a sane default so the app boots with zero config for retrieval;
only the final answer step needs an LLM key.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root (two levels up from this file) if present.
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class Settings:
    # Where uploaded/downloaded videos and extracted frames live.
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", str(_ROOT / "backend" / "data")))

    # Vector DB
    QDRANT_URL: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "moments")

    # Bring-your-own LLM (answer synthesis only)
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "").strip()
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "").strip()
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()

    # Visual ingestion
    FRAME_STRATEGY: str = os.getenv("FRAME_STRATEGY", "interval").strip().lower()
    FRAME_INTERVAL_SEC: float = _float("FRAME_INTERVAL_SEC", 2.0)
    SCENE_THRESHOLD: float = _float("SCENE_THRESHOLD", 0.4)
    MAX_FRAMES: int = _int("MAX_FRAMES", 400)
    CLIP_MODEL: str = os.getenv("CLIP_MODEL", "clip-ViT-B-32").strip()

    # Retrieval
    TOP_K: int = _int("TOP_K", 6)

    @property
    def video_dir(self) -> Path:
        return self.DATA_DIR / "videos"

    @property
    def frame_dir(self) -> Path:
        return self.DATA_DIR / "frames"

    def ensure_dirs(self) -> None:
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.frame_dir.mkdir(parents=True, exist_ok=True)

    @property
    def llm_configured(self) -> bool:
        # Local OpenAI-compatible servers often need no key, so a base_url alone counts.
        return bool(self.LLM_API_KEY or self.LLM_BASE_URL)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
