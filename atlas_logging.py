"""
atlas_logging.py — Structured logging for Atlas.

Every agent task gets a correlation id (task_id) propagated through log records.
Logs go to stderr and ~/.atlas/atlas.log.
"""
from __future__ import annotations

import logging
import sys
import threading
import uuid
from contextvars import ContextVar
from pathlib import Path

_LOG_DIR = Path.home() / ".atlas"
_LOG_FILE = _LOG_DIR / "atlas.log"

_task_id: ContextVar[str] = ContextVar("atlas_task_id", default="-")


class _AtlasFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.task_id = _task_id.get("-")  # type: ignore[attr-defined]
        return super().format(record)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root Atlas logging once at startup."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | task=%(task_id)s | %(message)s"
    formatter = _AtlasFormatter(fmt)

    root = logging.getLogger("atlas")
    if root.handlers:
        return
    root.setLevel(level)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(formatter)
    root.addHandler(sh)

    try:
        fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except OSError:
        pass


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"atlas.{name}")


def new_task_id() -> str:
    return uuid.uuid4().hex[:12]


def set_task_id(task_id: str) -> None:
    _task_id.set(task_id)


def task_scope(task_id: str | None = None):
    """Context manager: bind a correlation id for the current thread."""
    tid = task_id or new_task_id()

    class _Scope:
        def __enter__(self):
            self._token = _task_id.set(tid)
            return tid

        def __exit__(self, *_):
            _task_id.reset(self._token)

    return _Scope()


def install_thread_exception_hook(on_error=None) -> None:
    """Log uncaught exceptions in non-main threads."""
    def _hook(args: threading.ExceptHookArgs) -> None:
        log = get_logger("thread")
        log.error(
            "Uncaught exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        if on_error:
            try:
                on_error(args.exc_type, args.exc_value, args.exc_traceback)
            except Exception:
                pass

    threading.excepthook = _hook
