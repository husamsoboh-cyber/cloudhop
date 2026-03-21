"""Tests for multi-destination transfer feature."""

import http.server
import json
import threading
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from cloudhop.server import CSRF_TOKEN, CloudHopHandler
from cloudhop.transfer import TransferManager


@pytest.fixture
def manager(tmp_path):
    """Create a TransferManager with a temporary directory."""
    return TransferManager(cm_dir=str(tmp_path))


@pytest.fixture
def server_fixture(tmp_path):
    """Start a real CloudHop server on a random port."""
    mgr = TransferManager(cm_dir=str(tmp_path))
    mgr.log_file = str(tmp_path / "test.log")
    with open(mgr.log_file, "w") as f:
        f.write("2025/06/10 10:00:00 INFO  :\n")
    CloudHopHandler.manager = mgr
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CloudHopHandler)
    port = server.server_address[1]
    CloudHopHandler.actual_port = port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"port": port, "manager": mgr}
    server.shutdown()


def _post(port, path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST")
    req.add_header("Host", f"localhost:{port}")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-CSRF-Token", CSRF_TOKEN)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def _post_raw(port, path, body=None):
    """POST that returns (status_code, parsed_json) even for HTTP errors."""
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST")
    req.add_header("Host", f"localhost:{port}")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-CSRF-Token", CSRF_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------
# 1. test_multi_dest_single_destination: backward compatible
# ---------------------------------------------------------------------------


class TestMultiDestSingleDestination:
    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_single_dest_works(self, mock_exists, mock_popen, server_fixture):
        """A single destination via start-multi-dest behaves like regular start."""
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_popen.return_value = mock_proc

        port = server_fixture["port"]
        data = _post(
            port,
            "/api/wizard/start-multi-dest",
            {
                "source": "/tmp/src",
                "destinations": [{"remote": "local", "path": "/tmp/dst"}],
                "source_type": "local",
            },
        )
        assert data["ok"] is True
        assert data["total_destinations"] == 1
        assert data["queued"] == []  # nothing queued, only direct start


# ---------------------------------------------------------------------------
# 2. test_multi_dest_three_destinations: 1 direct start + 2 queue entries
# ---------------------------------------------------------------------------


class TestMultiDestThreeDestinations:
    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_three_dests_queue(self, mock_exists, mock_popen, server_fixture):
        """3 destinations: first starts directly, 2 go to queue."""
        mock_proc = MagicMock()
        mock_proc.pid = 5555
        mock_popen.return_value = mock_proc

        port = server_fixture["port"]
        mgr = server_fixture["manager"]
        data = _post(
            port,
            "/api/wizard/start-multi-dest",
            {
                "source": "/tmp/myfiles",
                "destinations": [
                    {"remote": "local", "path": "/tmp/dst1"},
                    {"remote": "drive", "path": "gdrive:Backup"},
                    {"remote": "s3", "path": "s3:mybucket/backup"},
                ],
                "source_type": "local",
            },
        )
        assert data["ok"] is True
        assert data["total_destinations"] == 3
        assert len(data["queued"]) == 2
        # Queue should have 2 waiting items
        items = mgr.queue_list()
        assert len(items) == 2
        assert items[0]["config"]["dest"] == "gdrive:Backup"
        assert items[1]["config"]["dest"] == "s3:mybucket/backup"
        assert all(i["status"] == "waiting" for i in items)


# ---------------------------------------------------------------------------
# 3. test_multi_dest_same_source: all transfers share the same source
# ---------------------------------------------------------------------------


class TestMultiDestSameSource:
    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_all_share_source(self, mock_exists, mock_popen, server_fixture):
        """All queued transfers have the same source."""
        mock_proc = MagicMock()
        mock_proc.pid = 7777
        mock_popen.return_value = mock_proc

        port = server_fixture["port"]
        mgr = server_fixture["manager"]
        data = _post(
            port,
            "/api/wizard/start-multi-dest",
            {
                "source": "/tmp/important",
                "destinations": [
                    {"remote": "local", "path": "/tmp/backup1"},
                    {"remote": "drive", "path": "gdrive:"},
                    {"remote": "onedrive", "path": "onedrive:"},
                ],
                "source_type": "local",
            },
        )
        assert data["ok"] is True
        items = mgr.queue_list()
        assert all(i["config"]["source"] == "/tmp/important" for i in items)


# ---------------------------------------------------------------------------
# 4. test_multi_dest_api_endpoint: POST accepts list of destinations
# ---------------------------------------------------------------------------


class TestMultiDestAPIEndpoint:
    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_accepts_destinations_list(self, mock_exists, mock_popen, server_fixture):
        """Endpoint accepts destinations as a list of {remote, path}."""
        mock_proc = MagicMock()
        mock_proc.pid = 8888
        mock_popen.return_value = mock_proc

        port = server_fixture["port"]
        data = _post(
            port,
            "/api/wizard/start-multi-dest",
            {
                "source": "/tmp/src",
                "destinations": [
                    {"remote": "drive", "path": "gdrive:backup"},
                    {"remote": "s3", "path": "s3:bucket/path"},
                ],
                "source_type": "local",
                "transfers": "4",
                "mode": "copy",
            },
        )
        assert data["ok"] is True
        assert "total_destinations" in data
        assert "queued" in data

    def test_rejects_empty_destinations(self, server_fixture):
        """Rejects empty destinations list."""
        port = server_fixture["port"]
        status, data = _post_raw(
            port,
            "/api/wizard/start-multi-dest",
            {"source": "/tmp/src", "destinations": []},
        )
        assert status == 400
        assert data["ok"] is False

    def test_rejects_missing_source(self, server_fixture):
        """Rejects missing source."""
        port = server_fixture["port"]
        status, data = _post_raw(
            port,
            "/api/wizard/start-multi-dest",
            {
                "source": "",
                "destinations": [{"remote": "drive", "path": "gdrive:"}],
            },
        )
        assert status == 400
        assert data["ok"] is False


# ---------------------------------------------------------------------------
# 5. test_multi_dest_max_five: rejects > 5 destinations
# ---------------------------------------------------------------------------


class TestMultiDestMaxFive:
    def test_rejects_six_destinations(self, server_fixture):
        """Rejects more than 5 destinations."""
        port = server_fixture["port"]
        dests = [{"remote": f"r{i}", "path": f"r{i}:path"} for i in range(6)]
        status, data = _post_raw(
            port,
            "/api/wizard/start-multi-dest",
            {"source": "/tmp/src", "destinations": dests},
        )
        assert status == 400
        assert "max 5" in data["msg"]

    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_accepts_five_destinations(self, mock_exists, mock_popen, server_fixture):
        """Accepts exactly 5 destinations."""
        mock_proc = MagicMock()
        mock_proc.pid = 9999
        mock_popen.return_value = mock_proc

        port = server_fixture["port"]
        dests = [{"remote": "local", "path": f"/tmp/dst{i}"} for i in range(5)]
        data = _post(
            port,
            "/api/wizard/start-multi-dest",
            {"source": "/tmp/src", "destinations": dests, "source_type": "local"},
        )
        assert data["ok"] is True
        assert data["total_destinations"] == 5
        assert len(data["queued"]) == 4  # first starts, 4 queued


# ---------------------------------------------------------------------------
# 6. test_multi_dest_queue_integration: queue_add called correctly
# ---------------------------------------------------------------------------


class TestMultiDestQueueIntegration:
    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_queue_entries_correct(self, mock_exists, mock_popen, server_fixture):
        """Each destination after the first gets queue_add called with correct config."""
        mock_proc = MagicMock()
        mock_proc.pid = 6666
        mock_popen.return_value = mock_proc

        port = server_fixture["port"]
        mgr = server_fixture["manager"]
        data = _post(
            port,
            "/api/wizard/start-multi-dest",
            {
                "source": "/tmp/docs",
                "destinations": [
                    {"remote": "local", "path": "/tmp/backup"},
                    {"remote": "drive", "path": "gdrive:Documents"},
                    {"remote": "s3", "path": "s3:mybucket"},
                    {"remote": "onedrive", "path": "onedrive:Backup"},
                ],
                "source_type": "local",
                "transfers": "4",
                "mode": "copy",
            },
        )
        assert data["ok"] is True
        assert len(data["queued"]) == 3
        items = mgr.queue_list()
        assert len(items) == 3
        # Check each queued item has correct dest and dest_type
        assert items[0]["config"]["dest"] == "gdrive:Documents"
        assert items[0]["config"]["dest_type"] == "drive"
        assert items[1]["config"]["dest"] == "s3:mybucket"
        assert items[1]["config"]["dest_type"] == "s3"
        assert items[2]["config"]["dest"] == "onedrive:Backup"
        assert items[2]["config"]["dest_type"] == "onedrive"
        # All share source, transfers, mode
        for item in items:
            assert item["config"]["source"] == "/tmp/docs"
            assert item["config"]["transfers"] == "4"
            assert item["config"]["mode"] == "copy"
