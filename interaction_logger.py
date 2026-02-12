"""Interaction logger for LLM Home Assistant.

Writes pretty-printed JSON entries to _logs/interactions_YYYY-MM-DD.json.
Entries are separated by a newline. Standalone module — no HA dependencies.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import threading
from datetime import datetime, timezone
from typing import Any

_LOGGER = logging.getLogger(__name__)

_LOG_DIR = os.path.join(os.path.dirname(__file__), "_logs")
_MAX_ENTRIES_PER_FILE = 500
_MAX_LOG_FILES = 7  # keep at most 7 days of logs
_write_lock = threading.Lock()


def new_log_entry() -> dict[str, Any]:
    """Return a blank log entry dict with a UTC timestamp."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request": {},
        "context": {},
        "llm_call": {},
        "actions": {},
        "execution": [],
        "timing": {},
    }


def _cleanup_old_logs(log_dir: str) -> None:
    """Delete oldest log files if more than _MAX_LOG_FILES exist."""
    try:
        files = sorted(
            (f for f in os.listdir(log_dir) if f.startswith("interactions_") and f.endswith(".json")),
        )
        while len(files) > _MAX_LOG_FILES:
            oldest = files.pop(0)
            path = os.path.join(log_dir, oldest)
            os.remove(path)
            _LOGGER.info("Deleted old log file: %s", oldest)
    except OSError:
        pass


def _count_entries(path: str) -> int:
    """Count log entries by looking for top-level timestamp markers."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip().startswith('"timestamp"'))
    except OSError:
        return 0


def _safe_serialize(obj: Any) -> Any:
    """json.default handler for non-serializable types."""
    if isinstance(obj, bytes):
        return f"<bytes len={len(obj)}>"
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def write_log_entry(entry: dict[str, Any]) -> None:
    """Append *entry* as pretty-printed JSON to today's log file.

    Thread-safe.  All errors are caught and logged — never raises.
    """
    try:
        with _write_lock:
            os.makedirs(_LOG_DIR, exist_ok=True)
            os.chmod(_LOG_DIR, 0o777)

            # Delete oldest log files beyond the retention limit
            _cleanup_old_logs(_LOG_DIR)

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            filename = f"interactions_{today}.json"
            filepath = os.path.join(_LOG_DIR, filename)

            # Check per-file entry limit
            if os.path.exists(filepath) and _count_entries(filepath) >= _MAX_ENTRIES_PER_FILE:
                _LOGGER.warning("Log file %s reached %d entries — skipping write", filename, _MAX_ENTRIES_PER_FILE)
                return

            new_file = not os.path.exists(filepath)
            block = json.dumps(entry, default=_safe_serialize, ensure_ascii=False, indent=2)
            with open(filepath, "a", encoding="utf-8") as f:
                # Separator between entries
                if not new_file and os.path.getsize(filepath) > 0:
                    f.write("\n")
                f.write(block + "\n")
            if new_file:
                os.chmod(filepath, 0o666)

    except Exception:
        _LOGGER.exception("Failed to write interaction log entry")
