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

exec(f"from {_pkg}.interaction_logger import new_log_entry, write_log_entry, _LOG_DIR, _MAX_ENTRIES_PER_FILE, _MAX_DIR_SIZE_BYTES, _count_lines, _dir_size, _safe_serialize, _write_lock")
new_log_entry = locals()["new_log_entry"]
write_log_entry = locals()["write_log_entry"]
_count_lines = locals()["_count_lines"]
_dir_size = locals()["_dir_size"]
_safe_serialize = locals()["_safe_serialize"]

exec(f"import {_pkg}.interaction_logger as _mod")
_mod = locals()["_mod"]


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
        # Should parse without error
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
# write_log_entry — JSONL format
# ===================================================================

class TestWriteLogEntry:
    def _redirect_log_dir(self, tmpdir):
        """Point the module's _LOG_DIR at a temp directory."""
        _mod._LOG_DIR = str(tmpdir)

    def _restore_log_dir(self):
        _mod._LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_logs")

    def test_creates_jsonl_file(self, tmp_path):
        self._redirect_log_dir(tmp_path)
        try:
            entry = new_log_entry()
            entry["request"] = {"type": "text", "user_prompt": "hello"}
            write_log_entry(entry)

            files = list(tmp_path.iterdir())
            assert len(files) == 1
            assert files[0].name.startswith("interactions_")
            assert files[0].name.endswith(".jsonl")
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
            lines = files[0].read_text().strip().split("\n")
            assert len(lines) == 3

            for i, line in enumerate(lines):
                obj = json.loads(line)
                assert obj["request"]["index"] == i
        finally:
            self._restore_log_dir()

    def test_each_line_is_valid_json(self, tmp_path):
        self._redirect_log_dir(tmp_path)
        try:
            for _ in range(5):
                write_log_entry(new_log_entry())

            content = list(tmp_path.iterdir())[0].read_text()
            for line in content.strip().split("\n"):
                json.loads(line)  # Should not raise
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
            lines = files[0].read_text().strip().split("\n")
            assert len(lines) == 5
        finally:
            _mod._MAX_ENTRIES_PER_FILE = old_max
            _mod._LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_logs")


# ===================================================================
# Max directory size guard
# ===================================================================

class TestMaxDirSizeGuard:
    def test_stops_when_dir_too_large(self, tmp_path):
        _mod._LOG_DIR = str(tmp_path)
        old_max = _mod._MAX_DIR_SIZE_BYTES
        _mod._MAX_DIR_SIZE_BYTES = 100  # very small
        try:
            # Write a large-ish entry to exceed 100 bytes
            entry = new_log_entry()
            entry["request"] = {"data": "x" * 200}
            write_log_entry(entry)  # Should succeed (first write)

            # Second write should be skipped because dir > 100 bytes
            write_log_entry(new_log_entry())

            files = list(tmp_path.iterdir())
            assert len(files) == 1
            lines = files[0].read_text().strip().split("\n")
            assert len(lines) == 1
        finally:
            _mod._MAX_DIR_SIZE_BYTES = old_max
            _mod._LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_logs")


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
            _mod._LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_logs")


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
            _mod._LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_logs")


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
            lines = files[0].read_text().strip().split("\n")
            assert len(lines) == 20
            # Each line should be valid JSON
            for line in lines:
                json.loads(line)
        finally:
            _mod._LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_logs")


# ===================================================================
# _count_lines / _dir_size helpers
# ===================================================================

class TestHelpers:
    def test_count_lines_empty(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert _count_lines(str(f)) == 0

    def test_count_lines_nonexistent(self):
        assert _count_lines("/nonexistent/file.txt") == 0

    def test_count_lines_some(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("a\nb\nc\n")
        assert _count_lines(str(f)) == 3

    def test_dir_size_empty(self, tmp_path):
        assert _dir_size(str(tmp_path)) == 0

    def test_dir_size_with_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world!")
        assert _dir_size(str(tmp_path)) == 11
