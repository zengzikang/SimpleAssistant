import logging
from pathlib import Path


def setup_logger(name: str = "simple_assistant", level=logging.INFO) -> logging.Logger:
    log_dir = Path.home() / ".simple_assistant" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )

        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        fh = logging.FileHandler(str(log_dir / "assistant.log"), encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger
