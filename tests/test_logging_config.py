import logging
from pathlib import Path

import clipsnstrips.logging_config as logging_config
from clipsnstrips.config import Settings


def test_default_log_directory_follows_output_directory(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        output_dir=tmp_path / "artifacts",
        log_dir=None,
    )
    assert settings.effective_log_dir == tmp_path / "artifacts" / "logs"


def test_configure_logging_writes_file_without_duplicates(tmp_path: Path) -> None:
    root = logging.getLogger()
    existing_handlers = list(root.handlers)
    existing_level = root.level
    logging_config._CONFIGURED = False
    try:
        path = logging_config.configure_logging(
            tmp_path,
            level="DEBUG",
            filename="test.log",
        )
        logging.getLogger("clipsnstrips.test").info("safe test message")
        for handler in root.handlers:
            handler.flush()

        assert "safe test message" in path.read_text(encoding="utf-8")
        handler_count = len(root.handlers)
        assert logging_config.configure_logging(tmp_path) == path
        assert len(root.handlers) == handler_count
    finally:
        for handler in list(root.handlers):
            if handler not in existing_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(existing_level)
        logging_config._CONFIGURED = False
        logging_config._LOG_PATH = None
