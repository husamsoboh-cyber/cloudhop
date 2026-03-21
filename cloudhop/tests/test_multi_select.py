"""Tests for multi-select files/folders in wizard with queue integration."""

import http.server
import json
import threading
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
    import urllib.request

    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST")
    req.add_header("Host", f"localhost:{port}")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-CSRF-Token", CSRF_TOKEN)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# 1. test_multi_select_single_item: backward compatible with single item
# ---------------------------------------------------------------------------


class TestMultiSelectSingleItem:
    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_single_path_start_multi_works(self, mock_exists, mock_popen, server_fixture):
        """A single path via start-multi behaves like regular start."""
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_popen.return_value = mock_proc

        port = server_fixture["port"]
        data = _post(
            port,
            "/api/wizard/start-multi",
            {
                "paths": ["/tmp/src1"],
                "dest": "/tmp/dst",
                "source_type": "local",
                "dest_type": "local",
            },
        )
        assert data["ok"] is True
        assert data["total_paths"] == 1
        assert data["queued"] == []  # nothing queued, only direct start


# ---------------------------------------------------------------------------
# 2. test_multi_select_multiple_items: 3 items -> 3 queue entries
# ---------------------------------------------------------------------------


class TestMultiSelectMultipleItems:
    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_three_paths_create_queue_entries(self, mock_exists, mock_popen, server_fixture):
        """3 paths: first starts directly, 2 go to queue."""
        mock_proc = MagicMock()
        mock_proc.pid = 5555
        mock_popen.return_value = mock_proc

        port = server_fixture["port"]
        mgr = server_fixture["manager"]
        data = _post(
            port,
            "/api/wizard/start-multi",
            {
                "paths": ["/tmp/a", "/tmp/b", "/tmp/c"],
                "dest": "/tmp/dst",
                "source_type": "local",
                "dest_type": "local",
            },
        )
        assert data["ok"] is True
        assert data["total_paths"] == 3
        assert len(data["queued"]) == 2
        # Queue should have 2 waiting items
        items = mgr.queue_list()
        assert len(items) == 2
        assert items[0]["config"]["source"] == "/tmp/b"
        assert items[1]["config"]["source"] == "/tmp/c"
        assert all(i["status"] == "waiting" for i in items)


# ---------------------------------------------------------------------------
# 3. test_multi_select_combined_size: total files and size combined correctly
# ---------------------------------------------------------------------------


class TestMultiSelectCombinedSize:
    @patch("subprocess.run")
    def test_combined_size_calculation(self, mock_run, server_fixture):
        """preview-multi combines file counts and sizes from all paths."""

        # Mock rclone size for each path
        def side_effect(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            path = cmd[2]  # rclone size <path> --json
            if path == "/tmp/a":
                result.stdout = json.dumps({"count": 100, "bytes": 1073741824})
            elif path == "/tmp/b":
                result.stdout = json.dumps({"count": 200, "bytes": 2147483648})
            elif path == "/tmp/c":
                result.stdout = json.dumps({"count": 50, "bytes": 536870912})
            else:
                result.stdout = json.dumps({"count": 0, "bytes": 0})
            return result

        mock_run.side_effect = side_effect
        port = server_fixture["port"]
        data = _post(
            port,
            "/api/wizard/preview-multi",
            {
                "paths": ["/tmp/a", "/tmp/b", "/tmp/c"],
                "source_type": "local",
                "dest_type": "local",
            },
        )
        assert data["ok"] is True
        assert data["count"] == 350  # 100 + 200 + 50
        assert data["size_bytes"] == 1073741824 + 2147483648 + 536870912
        assert data["num_sources"] == 3
        assert len(data["sources"]) == 3


# ---------------------------------------------------------------------------
# 4. test_multi_select_preview_endpoint: API accepts list of paths
# ---------------------------------------------------------------------------


class TestMultiSelectPreviewEndpoint:
    @patch("subprocess.run")
    def test_preview_multi_accepts_list(self, mock_run, server_fixture):
        """Preview-multi endpoint accepts and processes a list of paths."""
        result_mock = MagicMock()
        result_mock.returncode = 0
        result_mock.stdout = json.dumps({"count": 10, "bytes": 1048576})
        mock_run.return_value = result_mock

        port = server_fixture["port"]
        data = _post(
            port,
            "/api/wizard/preview-multi",
            {"paths": ["/tmp/x", "/tmp/y"], "source_type": "local", "dest_type": "local"},
        )
        assert data["ok"] is True
        assert data["count"] == 20  # 10 + 10
        assert data["size_bytes"] == 2097152  # 1M + 1M
        assert "estimated_duration" in data
        assert "sources" in data

    def test_preview_multi_rejects_empty_list(self, server_fixture):
        """Preview-multi rejects an empty paths list."""
        import urllib.error

        port = server_fixture["port"]
        try:
            _post(port, "/api/wizard/preview-multi", {"paths": []})
            raise AssertionError("Should have raised HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400

    def test_preview_multi_rejects_non_list(self, server_fixture):
        """Preview-multi rejects non-list paths."""
        import urllib.error

        port = server_fixture["port"]
        try:
            _post(port, "/api/wizard/preview-multi", {"paths": "not-a-list"})
            raise AssertionError("Should have raised HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400


# ---------------------------------------------------------------------------
# 5. test_multi_select_queue_integration: queue_add called for each
# ---------------------------------------------------------------------------


class TestMultiSelectQueueIntegration:
    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_queue_add_called_for_each_extra_path(self, mock_exists, mock_popen, server_fixture):
        """Each path after the first gets queue_add called."""
        mock_proc = MagicMock()
        mock_proc.pid = 7777
        mock_popen.return_value = mock_proc

        port = server_fixture["port"]
        mgr = server_fixture["manager"]
        paths = ["/tmp/p1", "/tmp/p2", "/tmp/p3", "/tmp/p4"]
        data = _post(
            port,
            "/api/wizard/start-multi",
            {
                "paths": paths,
                "dest": "/tmp/out",
                "source_type": "local",
                "dest_type": "local",
                "transfers": "4",
            },
        )
        assert data["ok"] is True
        assert len(data["queued"]) == 3  # p2, p3, p4
        items = mgr.queue_list()
        assert len(items) == 3
        queued_sources = [i["config"]["source"] for i in items]
        assert queued_sources == ["/tmp/p2", "/tmp/p3", "/tmp/p4"]
        # All should have correct dest
        assert all(i["config"]["dest"] == "/tmp/out" for i in items)
        # All should have transfers=4
        assert all(i["config"]["transfers"] == "4" for i in items)


# ---------------------------------------------------------------------------
# 6. test_multi_select_first_starts_immediately: first active, rest waiting
# ---------------------------------------------------------------------------


class TestMultiSelectFirstStartsImmediately:
    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_first_starts_rest_waiting(self, mock_exists, mock_popen, server_fixture):
        """First transfer starts immediately (ok=True, pid), rest are waiting in queue."""
        mock_proc = MagicMock()
        mock_proc.pid = 9876
        mock_popen.return_value = mock_proc

        port = server_fixture["port"]
        mgr = server_fixture["manager"]
        data = _post(
            port,
            "/api/wizard/start-multi",
            {
                "paths": ["/tmp/first", "/tmp/second", "/tmp/third"],
                "dest": "/tmp/dest",
                "source_type": "local",
                "dest_type": "local",
            },
        )
        assert data["ok"] is True
        assert "pid" in data
        # First transfer started directly (not in queue)
        items = mgr.queue_list()
        assert len(items) == 2
        assert all(i["status"] == "waiting" for i in items)
        assert items[0]["config"]["source"] == "/tmp/second"
        assert items[1]["config"]["source"] == "/tmp/third"
