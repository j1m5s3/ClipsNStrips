from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    output_dir: Path = Path("output")
    download_dir: Path = Path("downloads")
    youtube_api_key: str | None = None
    youtube_oauth_client_file: Path = Path("client_secret.json")
    youtube_oauth_token_file: Path = Path(".secrets/youtube_token.json")
    youtube_region: str = "US"
    youtube_category: str | None = None
    assemblyai_api_key: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    openai_api_key: str | None = None
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    log_dir: Path | None = None
    log_level: str = "INFO"
    log_filename: str = "clipsnstrips.log"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5
    owned_channel_ids: set[str] = Field(default_factory=set)
    min_clip_seconds: float = 15
    max_clip_seconds: float = 60

    def require(self, name: str) -> str:
        value = getattr(self, name)
        if not value:
            raise RuntimeError(f"Missing required setting: {name.upper()}")
        return str(value)

    @property
    def effective_log_dir(self) -> Path:
        return self.log_dir or self.output_dir / "logs"
