"""Integration tests for the CloudHop HTTP server.

These tests start a real HTTP server on a random port and send real HTTP
requests.  They cover:
- All POST routes with valid and invalid input
- Security: CSRF, Host header, path traversal, SSRF attempts
- Concurrency: 20+ simultaneous requests to /api/status
"""

import http.server
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import pytest

from cloudhop.server import CSRF_TOKEN, CloudHopHandler
from cloudhop.transfer import TransferManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server_fixture(tmp_path):
    """Start a real CloudHop server on a random port; tear it down after."""
    mgr = TransferManager(cm_dir=str(tmp_path))
    mgr.log_file = str(tmp_path / "test.log")
    # Write a minimal log so parse_current doesn't return "file not found"
    with open(mgr.log_file, "w") as f:
        f.write("2025/06/10 10:00:00 INFO  :\n")
        f.write("Transferred:   \t  100 MiB / 1.000 GiB, 10%, 50.000 MiB/s, ETA 30s\n")
        f.write("Transferred:            5 / 50, 10%\n")
        f.write("Errors:                 0\n")
        f.write("Elapsed time:      30.0s\n")

    CloudHopHandler.manager = mgr

    # Bind to port 0 to get a random available port
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CloudHopHandler)
    port = server.server_address[1]
    CloudHopHandler.actual_port = port

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield {"server": server, "port": port, "manager": mgr}

    server.shutdown()


def _get(port: int, path: str, host: str = "localhost") -> urllib.request.Request:
    """Build a GET request."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    req.add_header("Host", f"{host}:{port}")
    return req


def _post(
    port: int,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    csrf: Optional[str] = CSRF_TOKEN,
    host: str = "localhost",
) -> urllib.request.Request:
    """Build a POST request with CSRF token and JSON body."""
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST")
    req.add_header("Host", f"{host}:{port}")
    req.add_header("Content-Type", "application/json")
    if csrf:
        req.add_header("X-CSRF-Token", csrf)
    return req


def _fetch(req: urllib.request.Request, timeout: int = 5) -> Dict[str, Any]:
    """Execute request and return parsed JSON (retries once on connection reset).

    Python's ThreadingHTTPServer on macOS can occasionally reset connections
    during high concurrency, so we retry once on any ConnectionResetError --
    including ones raised from resp.read() after the status line was already
    received.
    """
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except (ConnectionResetError, ConnectionAbortedError):
            if attempt < 2:
                time.sleep(0.3)
                req = _rebuild_request(req)
            else:
                raise


def _fetch_raw(req: urllib.request.Request, timeout: int = 5):
    """Execute request and return (status_code, body_bytes).

    Retries on ConnectionResetError (Python's ThreadingHTTPServer can
    occasionally reset connections under high concurrency).
    """
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read()
            except (ConnectionResetError, ConnectionAbortedError):
                if attempt < 2:
                    time.sleep(0.3)
                    req = _rebuild_request(req)
                else:
                    raise
        except (ConnectionResetError, ConnectionAbortedError):
            if attempt < 2:
                time.sleep(0.3)
                req = _rebuild_request(req)
            else:
                raise
    return 0, b""


def _rebuild_request(req: urllib.request.Request) -> urllib.request.Request:
    """Rebuild a request after urlopen consumed it."""
    new_req = urllib.request.Request(req.full_url, data=req.data, method=req.get_method())
    for k, v in req.header_items():
        new_req.add_header(k, v)
    return new_req


# ---------------------------------------------------------------------------
# GET routes
# ---------------------------------------------------------------------------


class TestGetRoutes:
    def test_api_status(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_get(port, "/api/status"))
        assert "global_pct" in data
        assert "errors" in data
        assert isinstance(data.get("rclone_running"), bool)

    def test_api_wizard_status(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_get(port, "/api/wizard/status"))
        assert "rclone_installed" in data
        assert "remotes" in data

    def test_api_queue(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_get(port, "/api/queue"))
        assert "queue" in data
        assert isinstance(data["queue"], list)

    def test_api_schedule(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_get(port, "/api/schedule"))
        assert "enabled" in data

    def test_api_history(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_get(port, "/api/history"))
        assert isinstance(data, list)

    def test_dashboard_html(self, server_fixture):
        port = server_fixture["port"]
        status, body = _fetch_raw(_get(port, "/dashboard"))
        assert status == 200
        assert b"CloudHop" in body

    def test_wizard_html(self, server_fixture):
        port = server_fixture["port"]
        status, body = _fetch_raw(_get(port, "/wizard"))
        assert status == 200
        assert b"CloudHop" in body

    def test_root_redirect(self, server_fixture):
        port = server_fixture["port"]
        status, body = _fetch_raw(_get(port, "/"))
        assert status == 200
        assert b"CloudHop" in body

    def test_404_page(self, server_fixture):
        port = server_fixture["port"]
        status, body = _fetch_raw(_get(port, "/nonexistent"))
        assert status == 404
        assert b"404" in body


# ---------------------------------------------------------------------------
# POST routes - valid input
# ---------------------------------------------------------------------------


class TestPostRoutesValid:
    def test_pause(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_post(port, "/api/pause"))
        # No process running, so should return error
        assert data["ok"] is False

    def test_resume(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_post(port, "/api/resume"))
        # No command configured
        assert data["ok"] is False
        assert "No transfer" in data["msg"]

    def test_queue_add_and_list(self, server_fixture):
        port = server_fixture["port"]
        # Add
        data = _fetch(_post(port, "/api/queue/add", {"source": "gdrive:", "dest": "onedrive:"}))
        assert data["ok"] is True
        assert data["position"] == 1
        # List
        data = _fetch(_get(port, "/api/queue"))
        assert len(data["queue"]) == 1
        assert data["queue"][0]["source"] == "gdrive:"

    def test_queue_remove(self, server_fixture):
        port = server_fixture["port"]
        _fetch(_post(port, "/api/queue/add", {"source": "a:", "dest": "b:"}))
        data = _fetch(_post(port, "/api/queue/remove", {"index": 0}))
        assert data["ok"] is True

    def test_schedule_post(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(
            _post(
                port,
                "/api/schedule",
                {
                    "enabled": True,
                    "start_time": "22:00",
                    "end_time": "06:00",
                    "days": [0, 1, 2, 3, 4, 5, 6],
                },
            )
        )
        assert data["ok"] is True
        # Verify it was saved
        data = _fetch(_get(port, "/api/schedule"))
        assert data["enabled"] is True

    def test_verify_no_transfer(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_post(port, "/api/verify"))
        assert data["ok"] is False

    def test_bwlimit(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_post(port, "/api/bwlimit", {"rate": "10M"}))
        # No rclone running
        assert data["ok"] is False


# ---------------------------------------------------------------------------
# POST routes - invalid input
# ---------------------------------------------------------------------------


class TestPostRoutesInvalid:
    def test_queue_add_missing_source(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_post(port, "/api/queue/add", {"source": "", "dest": "x:"}))
        assert data["ok"] is False

    def test_queue_add_flag_injection(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(
            _post(port, "/api/queue/add", {"source": "--config=/etc/passwd", "dest": "x:"})
        )
        assert data["ok"] is False

    def test_queue_remove_invalid_index(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_post(port, "/api/queue/remove", {"index": 999}))
        assert data["ok"] is False

    def test_schedule_invalid_time(self, server_fixture):
        port = server_fixture["port"]
        status, body = _fetch_raw(
            _post(
                port, "/api/schedule", {"enabled": True, "start_time": "25:00", "end_time": "06:00"}
            )
        )
        assert status == 400

    def test_schedule_invalid_days(self, server_fixture):
        port = server_fixture["port"]
        status, body = _fetch_raw(
            _post(
                port,
                "/api/schedule",
                {"enabled": True, "start_time": "22:00", "end_time": "06:00", "days": [99]},
            )
        )
        assert status == 400

    def test_bwlimit_missing_rate(self, server_fixture):
        port = server_fixture["port"]
        status, body = _fetch_raw(_post(port, "/api/bwlimit", {"rate": ""}))
        assert status == 400

    def test_bwlimit_invalid_rate(self, server_fixture):
        port = server_fixture["port"]
        status, body = _fetch_raw(_post(port, "/api/bwlimit", {"rate": "--evil"}))
        assert status == 400

    def test_configure_remote_missing_fields(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_post(port, "/api/wizard/configure-remote", {"name": "", "type": ""}))
        assert data["ok"] is False

    def test_configure_remote_flag_injection(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(
            _post(port, "/api/wizard/configure-remote", {"name": "--evil", "type": "drive"})
        )
        assert data["ok"] is False

    def test_wizard_start_empty_body(self, server_fixture):
        port = server_fixture["port"]
        data = _fetch(_post(port, "/api/wizard/start", {}))
        assert data["ok"] is False

    def test_wizard_preview_invalid_source(self, server_fixture):
        port = server_fixture["port"]
        status, body = _fetch_raw(_post(port, "/api/wizard/preview", {"source": "--malicious"}))
        assert status == 400

    def test_history_resume_invalid_id(self, server_fixture):
        port = server_fixture["port"]
        status, body = _fetch_raw(_post(port, "/api/history/resume", {"id": "--evil"}))
        assert status == 400

    def test_post_invalid_json_body(self, server_fixture):
        """POST with non-JSON body."""
        port = server_fixture["port"]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/queue/add",
            data=b"not json",
            method="POST",
        )
        req.add_header("Host", f"localhost:{port}")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-CSRF-Token", CSRF_TOKEN)
        status, body = _fetch_raw(req)
        assert status == 400

    def test_post_array_body_rejected(self, server_fixture):
        """POST with JSON array instead of object."""
        port = server_fixture["port"]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/queue/add",
            data=b"[1,2,3]",
            method="POST",
        )
        req.add_header("Host", f"localhost:{port}")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-CSRF-Token", CSRF_TOKEN)
        status, body = _fetch_raw(req)
        assert status == 400


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestSecurity:
    def test_csrf_required_for_post(self, server_fixture):
        """POST without CSRF token returns 403."""
        port = server_fixture["port"]
        status, body = _fetch_raw(_post(port, "/api/pause", csrf=None))
        assert status == 403

    def test_csrf_wrong_token(self, server_fixture):
        """POST with wrong CSRF token returns 403."""
        port = server_fixture["port"]
        status, body = _fetch_raw(_post(port, "/api/pause", csrf="wrong-token"))
        assert status == 403

    def test_host_header_external_rejected(self, server_fixture):
        """Request with external Host header returns 403."""
        port = server_fixture["port"]
        status, body = _fetch_raw(_get(port, "/api/status", host="evil.com"))
        assert status == 403

    def test_host_header_subdomain_rejected(self, server_fixture):
        """Request with subdomain Host header returns 403."""
        port = server_fixture["port"]
        status, body = _fetch_raw(_get(port, "/api/status", host="sub.localhost"))
        assert status == 403

    def test_static_path_traversal(self, server_fixture):
        """Path traversal in static file serving returns 403 or 404."""
        port = server_fixture["port"]
        status, body = _fetch_raw(_get(port, "/static/../../etc/passwd"))
        assert status in (403, 404)

    def test_history_resume_path_traversal(self, server_fixture):
        """Path traversal in history resume returns 400 or 404 (never 200)."""
        port = server_fixture["port"]
        # The id is prefixed with "cloudhop_" and suffixed with "_state.json",
        # so we need a long traversal to escape the .cloudhop directory.
        status, body = _fetch_raw(
            _post(
                port,
                "/api/history/resume",
                {"id": "../../../../../../../../../tmp/evil"},
            )
        )
        assert status in (400, 404)

    def test_ssrf_backend_specifier_rejected(self, server_fixture):
        """SSRF via rclone backend specifier is rejected."""
        port = server_fixture["port"]
        data = _fetch(
            _post(
                port,
                "/api/queue/add",
                {"source": ":http,url=http://169.254.169.254:", "dest": "/tmp/x"},
            )
        )
        assert data["ok"] is False

    def test_cors_headers(self, server_fixture):
        """CORS headers are set correctly for allowed origins."""
        port = server_fixture["port"]
        req = _get(port, "/api/status")
        req.add_header("Origin", f"http://localhost:{port}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            cors = resp.headers.get("Access-Control-Allow-Origin", "")
            assert f"localhost:{port}" in cors

    def test_cors_blocked_for_other_origins(self, server_fixture):
        """CORS header not set for non-localhost origins."""
        port = server_fixture["port"]
        req = _get(port, "/api/status")
        req.add_header("Origin", "http://evil.com")
        with urllib.request.urlopen(req, timeout=5) as resp:
            cors = resp.headers.get("Access-Control-Allow-Origin", "")
            assert cors == "" or cors is None


# ---------------------------------------------------------------------------
# Concurrency: 20+ simultaneous requests
# ---------------------------------------------------------------------------


class TestConcurrentRequests:
    def test_20_concurrent_status_polls(self, server_fixture):
        """20 threads polling /api/status simultaneously."""
        port = server_fixture["port"]
        errors: List[str] = []
        results: List[Dict[str, Any]] = []
        barrier = threading.Barrier(20, timeout=5)

        def poll():
            try:
                barrier.wait()
                data = _fetch(_get(port, "/api/status"))
                results.append(data)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=poll) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        alive = [t for t in threads if t.is_alive()]
        assert not alive, f"{len(alive)} threads stuck"
        assert not errors, f"Errors: {errors}"
        assert len(results) == 20
        for r in results:
            assert "global_pct" in r

    def test_concurrent_mixed_get_post(self, server_fixture):
        """10 GET + 10 POST requests simultaneously."""
        port = server_fixture["port"]
        errors: List[str] = []
        barrier = threading.Barrier(20, timeout=5)

        def getter():
            try:
                barrier.wait()
                _fetch(_get(port, "/api/status"))
            except Exception as e:
                errors.append(f"GET: {e}")

        def poster():
            try:
                barrier.wait()
                _fetch(_post(port, "/api/queue/add", {"source": "a:", "dest": "b:"}))
            except Exception as e:
                errors.append(f"POST: {e}")

        threads = [threading.Thread(target=getter) for _ in range(10)]
        threads += [threading.Thread(target=poster) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        alive = [t for t in threads if t.is_alive()]
        assert not alive, f"{len(alive)} threads stuck"
        assert not errors, f"Errors: {errors}"
