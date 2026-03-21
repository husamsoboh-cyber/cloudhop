"""End-to-end integration tests for the CloudHop wizard flow.

Uses a real ThreadingHTTPServer to exercise wizard endpoints through HTTP.
"""

import http.server
import json
import os
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from cloudhop.server import CSRF_TOKEN, CloudHopHandler
from cloudhop.transfer import TransferManager

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def server_fixture(tmp_path):
    """Start a real CloudHop server on a random port; tear it down after."""
    mgr = TransferManager(cm_dir=str(tmp_path))
    mgr.log_file = str(tmp_path / "test.log")
    with open(mgr.log_file, "w") as f:
        f.write("2025/06/10 10:00:00 INFO  :\n")
        f.write("Transferred:   \t  100 MiB / 1.000 GiB, 10%, 50.000 MiB/s, ETA 30s\n")
        f.write("Transferred:            5 / 50, 10%\n")
        f.write("Errors:                 0\n")
        f.write("Elapsed time:      30.0s\n")

    CloudHopHandler.manager = mgr

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CloudHopHandler)
    port = server.server_address[1]
    CloudHopHandler.actual_port = port

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield {"server": server, "port": port, "manager": mgr}

    server.shutdown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5


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


def _rebuild_request(req):
    new_req = urllib.request.Request(req.full_url, data=req.data, method=req.get_method())
    for k, v in req.header_items():
        new_req.add_header(k, v)
    return new_req


def _fetch(req, timeout=5):
    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            if attempt < _MAX_RETRIES - 1:
                time.sleep(0.5)
                req = _rebuild_request(req)
            else:
                raise


def _fetch_raw(req, timeout=5):
    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(0.5)
                    req = _rebuild_request(req)
                else:
                    raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            if attempt < _MAX_RETRIES - 1:
                time.sleep(0.5)
                req = _rebuild_request(req)
            else:
                raise
    return 0, b""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWizardE2E:
    """End-to-end tests for wizard endpoints over a real HTTP server."""

    def test_wizard_page_returns_html(self, server_fixture):
        """GET /wizard returns 200 with HTML containing 'CloudHop'."""
        port = server_fixture["port"]
        req = _get(port, "/wizard")
        status, body = _fetch_raw(req)
        assert status == 200
        assert b"CloudHop" in body

    def test_validate_path_valid(self, server_fixture):
        """POST /api/wizard/validate-path with a valid path returns exists info."""
        port = server_fixture["port"]
        valid_path = os.path.expanduser("~")
        req = _post(port, "/api/wizard/validate-path", body={"path": valid_path})
        data = _fetch(req)
        assert "exists" in data

    def test_validate_path_invalid(self, server_fixture):
        """POST /api/wizard/validate-path with empty path returns not-found."""
        port = server_fixture["port"]
        req = _post(port, "/api/wizard/validate-path", body={"path": ""})
        data = _fetch(req)
        assert data["exists"] is False
        assert data["is_directory"] is False

    @patch("cloudhop.server.subprocess.run")
    def test_wizard_preview_valid_source(self, mock_run, server_fixture):
        """POST /api/wizard/preview with mocked rclone returns size info."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"count": 42, "bytes": 1073741824}),
            stderr="",
        )
        port = server_fixture["port"]
        req = _post(
            port,
            "/api/wizard/preview",
            body={"source": "/tmp/a", "source_type": "local"},
        )
        data = _fetch(req)
        assert data.get("ok") is True
        assert "count" in data
        assert "size" in data

    @patch("os.path.exists", return_value=True)
    @patch("subprocess.Popen")
    def test_wizard_start_valid_params(self, mock_popen, mock_exists, server_fixture):
        """POST /api/wizard/start with valid params starts a transfer."""
        mock_proc = MagicMock()
        mock_proc.pid = 9999
        mock_popen.return_value = mock_proc
        port = server_fixture["port"]
        req = _post(
            port,
            "/api/wizard/start",
            body={
                "source": "/tmp/a",
                "dest": "/tmp/b",
                "source_type": "local",
                "dest_type": "local",
            },
        )
        data = _fetch(req)
        assert "ok" in data

    def test_csrf_required_on_wizard_start(self, server_fixture):
        """POST /api/wizard/start without CSRF token returns 403."""
        port = server_fixture["port"]
        req = _post(
            port,
            "/api/wizard/start",
            body={
                "source": "/tmp/a",
                "dest": "/tmp/b",
                "source_type": "local",
                "dest_type": "local",
            },
            csrf=None,
        )
        status, _ = _fetch_raw(req)
        assert status == 403

    def test_csrf_required_on_validate_path(self, server_fixture):
        """POST /api/wizard/validate-path without CSRF token returns 403."""
        port = server_fixture["port"]
        req = _post(
            port,
            "/api/wizard/validate-path",
            body={"path": "/tmp"},
            csrf=None,
        )
        status, _ = _fetch_raw(req)
        assert status == 403

    def test_csrf_required_on_preview(self, server_fixture):
        """POST /api/wizard/preview without CSRF token returns 403."""
        port = server_fixture["port"]
        req = _post(
            port,
            "/api/wizard/preview",
            body={"source": "/tmp/a", "source_type": "local"},
            csrf=None,
        )
        status, _ = _fetch_raw(req)
        assert status == 403
