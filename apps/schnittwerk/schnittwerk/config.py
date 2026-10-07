"""Runtime configuration, read once from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    access_token: str
    whisper_model: str
    whisper_device: str
    whisper_compute: str
    claude_model: str
    max_upload_mb: int
    max_duration_min: int
    job_ttl_hours: int

    @property
    def claude_enabled(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def load() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("SCHNITTWERK_DATA_DIR", "./data")).resolve(),
        access_token=os.environ.get("SCHNITTWERK_TOKEN", ""),
        whisper_model=os.environ.get("WHISPER_MODEL", "small"),
        whisper_device=os.environ.get("WHISPER_DEVICE", "cpu"),
        whisper_compute=os.environ.get("WHISPER_COMPUTE", "int8"),
        claude_model=os.environ.get("SCHNITTWERK_CLAUDE_MODEL", "claude-opus-5-5"),
        max_upload_mb=_int("SCHNITTWERK_MAX_UPLOAD_MB", 2048),
        max_duration_min=_int("SCHNITTWERK_MAX_DURATION_MIN", 180),
        job_ttl_hours=_int("SCHNITTWERK_JOB_TTL_HOURS", 24),
    )


settings = load()
