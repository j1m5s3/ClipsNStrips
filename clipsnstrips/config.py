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
    scene_context_model: str = "gemini-3.6-flash"
    openai_api_key: str | None = None
    openai_image_model: str = "gpt-image-1"
    openai_image_fidelity: str = Field(default="high", pattern="^(low|high)$")
    art_moderation_fallback_enabled: bool = True
    art_moderation_final_action: str = Field(
        default="placeholder",
        pattern="^(placeholder|fail)$",
    )
    reference_frame_count: int = Field(default=6, ge=1, le=15)
    reference_frame_max_width: int = Field(default=1024, ge=256, le=2048)
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
    highlight_seconds_per_candidate: float = 120
    highlight_min_candidates: int = 3
    highlight_max_candidates: int = 12
    art_seconds_per_panel: float = 8
    art_min_panels: int = 3
    art_max_panels: int = 12

    def require(self, name: str) -> str:
        value = getattr(self, name)
        if not value:
            raise RuntimeError(f"Missing required setting: {name.upper()}")
        return str(value)

    @property
    def effective_log_dir(self) -> Path:
        return self.log_dir or self.output_dir / "logs"
