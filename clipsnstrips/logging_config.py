from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False
_LOG_PATH: Path | None = None


def configure_logging(
    log_dir: Path,
    *,
    level: str = "INFO",
    filename: str = "clipsnstrips.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> Path:
    """Configure console and rotating file logging once per process."""
    global _CONFIGURED, _LOG_PATH

    log_path = log_dir / filename
    if _CONFIGURED:
        return _LOG_PATH or log_path

    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid LOG_LEVEL: {level}")

    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(numeric_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(numeric_level)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    for library in (
        "assemblyai",
        "google_genai",
        "googleapiclient",
        "httpcore",
        "httpx",
        "openai",
    ):
        logging.getLogger(library).setLevel(logging.WARNING)
    _CONFIGURED = True
    _LOG_PATH = log_path
    logging.getLogger(__name__).info("Logging initialized path=%s level=%s", log_path, level)
    return log_path
