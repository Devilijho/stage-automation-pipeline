"""Rotating file logger for StageBot modules (writes diagnostic logs to data/stagebot.log)."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import DATA_DIR

LOG_PATH = DATA_DIR / "stagebot.log"


def get_logger(name: str, log_path: Path = LOG_PATH) -> logging.Logger:
    """Return a logger configured with a rotating file handler under data/stagebot.log."""
    logger = logging.getLogger(f"stagebot.{name}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())
    return logger
