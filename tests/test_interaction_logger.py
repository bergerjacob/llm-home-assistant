"""Unit tests for interaction_logger.py.

run_tests.py stubs all HA/openai/pydantic imports before this runs.
"""
import json
import os
import sys
import tempfile
import threading

# Add the PARENT of the repo root so we can import the package by directory name
_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_parent = os.path.dirname(_repo)
_pkg = os.path.basename(_repo)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

exec(f"from {_pkg}.interaction_logger import new_log_entry, write_log_entry, _LOG_DIR, _MAX_ENTRIES_PER_FILE, _MAX_LOG_FILES, _count_entries, _cleanup_old_logs, _safe_serialize, _write_lock")
new_log_entry = locals()["new_log_entry"]
write_log_entry = locals()["write_log_entry"]
_count_entries = locals()["_count_entries"]
_cleanup_old_logs = locals()["_cleanup_old_logs"]
_safe_serialize = locals()["_safe_serialize"]

exec(f"import {_pkg}.interaction_logger as _mod")
_mod = locals()["_mod"]

_DEFAULT_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_logs")


# ===================================================================
# new_log_entry structure
# ===================================================================

class TestNewLogEntry:
    def test_has_required_keys(self):
        entry = new_log_entry()
        for key in ("timestamp", "request", "context", "llm_call", "actions", "execution", "timing"):
            assert key in entry, f"missing key: {key}"

    def test_timestamp_is_iso(self):
        entry = new_log_entry()
        from datetime import datetime
        datetime.fromisoformat(entry["timestamp"])

    def test_execution_is_list(self):
        entry = new_log_entry()
        assert isinstance(entry["execution"], list)

    def test_sections_are_dicts(self):
        entry = new_log_entry()
        for key in ("request", "context", "llm_call", "actions", "timing"):
            assert isinstance(entry[key], dict)


# ===================================================================
# write_log_entry — pretty-printed JSON
# ===================================================================

class TestWriteLogEntry:
    def _redirect_log_dir(self, tmpdir):
        _mod._LOG_DIR = str(tmpdir)

    def _restore_log_dir(self):
        _mod._LOG_DIR = _DEFAULT_LOG_DIR

    def test_creates_json_file(self, tmp_path):
        self._redirect_log_dir(tmp_path)
        try:
            entry = new_log_entry()
            entry["request"] = {"type": "text", "user_prompt": "hello"}
            write_log_entry(entry)

            files = list(tmp_path.iterdir())
            assert len(files) == 1
            assert files[0].name.startswith("interactions_")
            assert files[0].name.endswith(".json")
        finally:
            self._restore_log_dir()

    def test_single_entry_is_valid_json(self, tmp_path):
        self._redirect_log_dir(tmp_path)
        try:
            entry = new_log_entry()
            entry["request"] = {"type": "text", "user_prompt": "test"}
            write_log_entry(entry)

            content = list(tmp_path.iterdir())[0].read_text()
            obj = json.loads(content.strip())
            assert obj["request"]["user_prompt"] == "test"
        finally:
            self._restore_log_dir()

    def test_pretty_printed(self, tmp_path):
        self._redirect_log_dir(tmp_path)
        try:
            write_log_entry(new_log_entry())
            content = list(tmp_path.iterdir())[0].read_text()
            # Pretty-printed JSON has multiple lines
            assert content.count("\n") > 5
        finally:
            self._restore_log_dir()

    def test_appends_multiple_entries(self, tmp_path):
        self._redirect_log_dir(tmp_path)
        try:
            for i in range(3):
                entry = new_log_entry()
                entry["request"] = {"index": i}
                write_log_entry(entry)

            files = list(tmp_path.iterdir())
            assert len(files) == 1
            # Count entries via the helper
            assert _count_entries(str(files[0])) == 3
        finally:
            self._restore_log_dir()


# ===================================================================
# Max entries guard
# ===================================================================

class TestMaxEntriesGuard:
    def test_stops_at_max_entries(self, tmp_path):
        _mod._LOG_DIR = str(tmp_path)
        old_max = _mod._MAX_ENTRIES_PER_FILE
        _mod._MAX_ENTRIES_PER_FILE = 5
        try:
            for _ in range(10):
                write_log_entry(new_log_entry())

            files = list(tmp_path.iterdir())
            assert len(files) == 1
            assert _count_entries(str(files[0])) == 5
        finally:
            _mod._MAX_ENTRIES_PER_FILE = old_max
            _mod._LOG_DIR = _DEFAULT_LOG_DIR


# ===================================================================
# Old log file cleanup
# ===================================================================

class TestCleanupOldLogs:
    def test_deletes_oldest_files_beyond_limit(self, tmp_path):
        # Create 10 fake log files with sequential dates
        for i in range(10):
            (tmp_path / f"interactions_2026-01-{i+1:02d}.json").write_text("{}")

        old_max = _mod._MAX_LOG_FILES
        _mod._MAX_LOG_FILES = 3
        try:
            _cleanup_old_logs(str(tmp_path))
            remaining = sorted(f.name for f in tmp_path.iterdir())
            assert len(remaining) == 3
            # Should keep the 3 newest (sorted last)
            assert remaining == [
                "interactions_2026-01-08.json",
                "interactions_2026-01-09.json",
                "interactions_2026-01-10.json",
            ]
        finally:
            _mod._MAX_LOG_FILES = old_max

    def test_no_delete_when_under_limit(self, tmp_path):
        (tmp_path / "interactions_2026-01-01.json").write_text("{}")
        (tmp_path / "interactions_2026-01-02.json").write_text("{}")
        _cleanup_old_logs(str(tmp_path))
        assert len(list(tmp_path.iterdir())) == 2

    def test_ignores_non_log_files(self, tmp_path):
        (tmp_path / "interactions_2026-01-01.json").write_text("{}")
        (tmp_path / "other_file.txt").write_text("keep me")

        old_max = _mod._MAX_LOG_FILES
        _mod._MAX_LOG_FILES = 1
        try:
            _cleanup_old_logs(str(tmp_path))
            remaining = [f.name for f in tmp_path.iterdir()]
            assert "other_file.txt" in remaining
            assert "interactions_2026-01-01.json" in remaining
        finally:
            _mod._MAX_LOG_FILES = old_max


# ===================================================================
# Date-based filenames
# ===================================================================

class TestDateFilenames:
    def test_filename_contains_today(self, tmp_path):
        from datetime import datetime, timezone
        _mod._LOG_DIR = str(tmp_path)
        try:
            write_log_entry(new_log_entry())
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            files = list(tmp_path.iterdir())
            assert any(today in f.name for f in files)
        finally:
            _mod._LOG_DIR = _DEFAULT_LOG_DIR


# ===================================================================
# Non-serializable type handling
# ===================================================================

class TestSafeSerialize:
    def test_bytes_handled(self):
        result = _safe_serialize(b"hello")
        assert "bytes" in result and "5" in result

    def test_set_becomes_list(self):
        result = _safe_serialize({"a", "b"})
        assert isinstance(result, list)

    def test_arbitrary_object(self):
        class Foo:
            pass
        result = _safe_serialize(Foo())
        assert isinstance(result, str)

    def test_entry_with_bytes_writes(self, tmp_path):
        _mod._LOG_DIR = str(tmp_path)
        try:
            entry = new_log_entry()
            entry["request"]["audio_data"] = b"\x00\x01\x02"
            entry["request"]["some_set"] = {1, 2, 3}
            write_log_entry(entry)

            content = list(tmp_path.iterdir())[0].read_text()
            obj = json.loads(content.strip())
            assert "bytes" in obj["request"]["audio_data"]
        finally:
            _mod._LOG_DIR = _DEFAULT_LOG_DIR


# ===================================================================
# Thread safety
# ===================================================================

class TestThreadSafety:
    def test_concurrent_writes(self, tmp_path):
        _mod._LOG_DIR = str(tmp_path)
        try:
            threads = []
            for i in range(20):
                entry = new_log_entry()
                entry["request"] = {"thread": i}
                t = threading.Thread(target=write_log_entry, args=(entry,))
                threads.append(t)

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            files = list(tmp_path.iterdir())
            assert len(files) == 1
            assert _count_entries(str(files[0])) == 20
        finally:
            _mod._LOG_DIR = _DEFAULT_LOG_DIR


# ===================================================================
# _count_entries helper
# ===================================================================

class TestHelpers:
    def test_count_entries_empty(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert _count_entries(str(f)) == 0

    def test_count_entries_nonexistent(self):
        assert _count_entries("/nonexistent/file.txt") == 0

    def test_count_entries_pretty_json(self, tmp_path):
        _mod._LOG_DIR = str(tmp_path)
        try:
            for _ in range(3):
                write_log_entry(new_log_entry())
            files = list(tmp_path.iterdir())
            assert _count_entries(str(files[0])) == 3
        finally:
            _mod._LOG_DIR = _DEFAULT_LOG_DIR
