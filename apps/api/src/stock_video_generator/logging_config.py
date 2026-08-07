from __future__ import annotations

import json
import logging
import traceback
from datetime import UTC, datetime
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "job_id": getattr(record, "job_id", None),
            "simulation_id": getattr(record, "simulation_id", None),
            "render_id": getattr(record, "render_id", None),
            "provider": getattr(record, "provider", None),
            "stage": getattr(record, "stage", None),
            "message": record.getMessage(),
            "exception": None,
        }
        if record.exc_info:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if getattr(root, "_stock_video_configured", False):
        return

    formatter = JsonFormatter()
    app_handler = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    app_handler.setFormatter(formatter)
    app_handler.setLevel(logging.INFO)
    error_handler = logging.FileHandler(log_dir / "error.log", encoding="utf-8")
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    root.addHandler(app_handler)
    root.addHandler(error_handler)
    root._stock_video_configured = True  # type: ignore[attr-defined]


def attach_job_log(log_dir: Path, job_id: str, logger: logging.Logger) -> logging.Handler:
    path = log_dir / "jobs" / f"{job_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return handler
