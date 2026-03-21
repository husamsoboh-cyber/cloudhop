"""Tests for CloudHop transfer presets."""

import http.server
import json
import os
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock

import pytest

from cloudhop import presets
from cloudhop.server import CSRF_TOKEN, CloudHopHandler
from cloudhop.transfer import TransferManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_presets(tmp_path, monkeypatch):
    """Point presets at a temp directory so tests don't touch real ~/.cloudhop."""
    presets_file = str(tmp_path / "presets.json")
    monkeypatch.setattr(presets, "_PRESETS_FILE", presets_file)


SAMPLE_CONFIG = {
    "source": "/home/user/Documents",
    "dest": "gdrive:Backup/Documents",
    "transfers": "8",
    "excludes": [],
    "source_type": "local",
    "dest_type": "drive",
    "bw_limit": "",
    "checksum": False,
    "fast_list": True,
    "mode": "copy",
}


# ---------------------------------------------------------------------------
# 1. test_save_preset
# ---------------------------------------------------------------------------


class TestSavePreset:
    def test_save_and_list(self):
        pid = presets.save_preset("Docs to GDrive", SAMPLE_CONFIG)
        assert isinstance(pid, str) and len(pid) == 16
        result = presets.list_presets()
        assert len(result) == 1
        assert result[0]["name"] == "Docs to GDrive"
        assert result[0]["config"] == SAMPLE_CONFIG
        assert result[0]["use_count"] == 0


# ---------------------------------------------------------------------------
# 2. test_delete_preset
# ---------------------------------------------------------------------------


class TestDeletePreset:
    def test_delete_existing(self):
        pid = presets.save_preset("Temp", SAMPLE_CONFIG)
        assert presets.delete_preset(pid) is True
        assert presets.list_presets() == []

    def test_delete_nonexistent(self):
        assert presets.delete_preset("0000000000000000") is False


# ---------------------------------------------------------------------------
# 3. test_run_preset
# ---------------------------------------------------------------------------


class TestRunPreset:
    def test_run_starts_transfer(self, tmp_path):
        pid = presets.save_preset("Run me", SAMPLE_CONFIG)
        mgr = MagicMock()
        mgr.start_transfer.return_value = {"ok": True, "pid": 12345}
        result = presets.run_preset(pid, mgr)
        assert result["ok"] is True
        assert result["preset_id"] == pid
        mgr.start_transfer.assert_called_once_with(SAMPLE_CONFIG)

    def test_run_nonexistent(self):
        mgr = MagicMock()
        result = presets.run_preset("0000000000000000", mgr)
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# 4. test_preset_persistence
# ---------------------------------------------------------------------------


class TestPresetPersistence:
    def test_save_reload(self, tmp_path, monkeypatch):
        pid = presets.save_preset("Persist Test", SAMPLE_CONFIG)
        # Clear the in-memory state by just re-reading
        loaded = presets.list_presets()
        assert len(loaded) == 1
        assert loaded[0]["preset_id"] == pid

    def test_corrupt_json(self, tmp_path, monkeypatch):
        pf = presets._PRESETS_FILE
        os.makedirs(os.path.dirname(pf), exist_ok=True)
        with open(pf, "w") as f:
            f.write("NOT VALID JSON{{{")
        result = presets.list_presets()
        assert result == []


# ---------------------------------------------------------------------------
# 5. test_preset_use_count
# ---------------------------------------------------------------------------


class TestPresetUseCount:
    def test_use_count_increments(self):
        pid = presets.save_preset("Counter", SAMPLE_CONFIG)
        mgr = MagicMock()
        mgr.start_transfer.return_value = {"ok": True}
        presets.run_preset(pid, mgr)
        presets.run_preset(pid, mgr)
        p = presets.get_preset(pid)
        assert p["use_count"] == 2
        assert p["last_used"] is not None


# ---------------------------------------------------------------------------
# 6. test_preset_api_endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def server_fixture(tmp_path):
    """Start a real CloudHop server on a random port."""
    mgr = TransferManager(cm_dir=str(tmp_path))
    mgr.log_file = str(tmp_path / "test.log")
    with open(mgr.log_file, "w") as f:
        f.write("2025/06/10 10:00:00 INFO  :\n")
        f.write("Elapsed time:      1.0s\n")
    CloudHopHandler.manager = mgr
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CloudHopHandler)
    port = server.server_address[1]
    CloudHopHandler.actual_port = port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2):
                break
        except Exception:
            time.sleep(0.1)
    yield {"server": server, "port": port, "manager": mgr}
    server.shutdown()
    thread.join(timeout=5)


def _get(port, path, host="localhost"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    req.add_header("Host", f"{host}:{port}")
    return req


def _post(port, path, body=None, csrf=CSRF_TOKEN, host="localhost"):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST")
    req.add_header("Host", f"{host}:{port}")
    req.add_header("Content-Type", "application/json")
    if csrf:
        req.add_header("X-CSRF-Token", csrf)
    return req


def _delete(port, path, csrf=CSRF_TOKEN, host="localhost"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="DELETE")
    req.add_header("Host", f"{host}:{port}")
    req.add_header("Content-Type", "application/json")
    if csrf:
        req.add_header("X-CSRF-Token", csrf)
    return req


class TestPresetAPIEndpoints:
    def test_post_save_preset(self, server_fixture):
        port = server_fixture["port"]
        resp = urllib.request.urlopen(
            _post(port, "/api/presets", {"name": "Test", "config": SAMPLE_CONFIG})
        )
        data = json.loads(resp.read())
        assert data["ok"] is True
        assert "preset_id" in data

    def test_get_list_presets(self, server_fixture):
        port = server_fixture["port"]
        # Save one first
        urllib.request.urlopen(
            _post(port, "/api/presets", {"name": "List Test", "config": SAMPLE_CONFIG})
        )
        resp = urllib.request.urlopen(_get(port, "/api/presets"))
        data = json.loads(resp.read())
        assert len(data["presets"]) >= 1

    def test_get_single_preset(self, server_fixture):
        port = server_fixture["port"]
        resp = urllib.request.urlopen(
            _post(port, "/api/presets", {"name": "Single", "config": SAMPLE_CONFIG})
        )
        pid = json.loads(resp.read())["preset_id"]
        resp2 = urllib.request.urlopen(_get(port, f"/api/presets/{pid}"))
        data = json.loads(resp2.read())
        assert data["name"] == "Single"

    def test_delete_preset_api(self, server_fixture):
        port = server_fixture["port"]
        resp = urllib.request.urlopen(
            _post(port, "/api/presets", {"name": "Del", "config": SAMPLE_CONFIG})
        )
        pid = json.loads(resp.read())["preset_id"]
        resp2 = urllib.request.urlopen(_delete(port, f"/api/presets/{pid}"))
        data = json.loads(resp2.read())
        assert data["ok"] is True
        # Verify gone
        resp3 = urllib.request.urlopen(_get(port, "/api/presets"))
        assert len(json.loads(resp3.read())["presets"]) == 0

    def test_run_preset_api(self, server_fixture):
        port = server_fixture["port"]
        resp = urllib.request.urlopen(
            _post(port, "/api/presets", {"name": "RunAPI", "config": SAMPLE_CONFIG})
        )
        pid = json.loads(resp.read())["preset_id"]
        # Run will fail because source doesn't exist, but endpoint should respond
        try:
            urllib.request.urlopen(_post(port, f"/api/presets/{pid}/run"))
        except urllib.error.HTTPError:
            pass  # Expected: transfer start may fail
        # Verify use_count incremented
        resp2 = urllib.request.urlopen(_get(port, f"/api/presets/{pid}"))
        data = json.loads(resp2.read())
        assert data["use_count"] == 1

    def test_preset_csrf_required(self, server_fixture):
        port = server_fixture["port"]
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(
                _post(port, "/api/presets", {"name": "No CSRF", "config": {}}, csrf=None)
            )
        assert exc.value.code == 403


# ---------------------------------------------------------------------------
# 7. test_preset_duplicate_name
# ---------------------------------------------------------------------------


class TestPresetDuplicateName:
    def test_two_presets_same_name_different_ids(self):
        pid1 = presets.save_preset("Same Name", SAMPLE_CONFIG)
        pid2 = presets.save_preset("Same Name", SAMPLE_CONFIG)
        assert pid1 != pid2
        result = presets.list_presets()
        assert len(result) == 2
        names = [p["name"] for p in result]
        assert names == ["Same Name", "Same Name"]
