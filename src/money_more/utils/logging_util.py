"""统一日志：带 run_id 的简单配置。"""

from __future__ import annotations

import logging
import sys


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("money_more")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
    )
    logger.addHandler(handler)
    return logger


def get_logger(run_id: int | None = None) -> logging.LoggerAdapter:
    base = logging.getLogger("money_more")
    if not base.handlers:
        setup_logging()
    return logging.LoggerAdapter(base, {"run_id": run_id})
