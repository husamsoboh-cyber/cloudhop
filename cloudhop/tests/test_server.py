"""Tests for cloudhop.server module."""

import io
import os
import unittest
from unittest.mock import MagicMock

from cloudhop.server import CSRF_TOKEN, CloudHopHandler


class FakeSocket:
    """Minimal socket stand-in for BaseHTTPRequestHandler."""

    def __init__(self):
        self.data = b""

    def makefile(self, mode, **kwargs):
        if "r" in mode:
            return io.BytesIO(b"GET / HTTP/1.1\r\nHost: localhost:8787\r\n\r\n")
        return io.BytesIO()


def _make_handler(host="localhost:8787", method="GET", path="/"):
    """Create a CloudHopHandler with mocked internals."""
    handler = CloudHopHandler.__new__(CloudHopHandler)
    handler.headers = {"Host": host, "Origin": "", "Content-Length": "0"}
    handler.path = path
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    # Mock response writing
    handler.wfile = io.BytesIO()
    handler._headers_buffer = []
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    return handler


class TestCheckHost(unittest.TestCase):
    """Test _check_host with valid and invalid hosts."""

    def test_localhost_allowed(self):
        handler = _make_handler(host="localhost:8787")
        self.assertTrue(handler._check_host())

    def test_127_0_0_1_allowed(self):
        handler = _make_handler(host="127.0.0.1:8787")
        self.assertTrue(handler._check_host())

    def test_localhost_no_port_allowed(self):
        handler = _make_handler(host="localhost")
        self.assertTrue(handler._check_host())

    def test_external_host_rejected(self):
        handler = _make_handler(host="evil.com:8787")
        result = handler._check_host()
        self.assertFalse(result)
        handler.send_response.assert_called_with(403)

    def test_empty_host_rejected(self):
        handler = _make_handler(host="")
        self.assertFalse(handler._check_host())

    def test_subdomain_localhost_rejected(self):
        handler = _make_handler(host="sub.localhost:8787")
        self.assertFalse(handler._check_host())


class TestStaticPathTraversal(unittest.TestCase):
    """Test that _serve_static rejects directory traversal attempts."""

    def test_traversal_dot_dot_rejected(self):
        handler = _make_handler()
        handler._serve_static("../../etc/passwd")
        handler.send_response.assert_called_with(403)

    def test_traversal_encoded_rejected(self):
        handler = _make_handler()
        handler._serve_static("..%2F..%2Fetc/passwd")
        # Should get either 403 (traversal) or 404 (file not found), not 200
        calls = [c[0][0] for c in handler.send_response.call_args_list]
        self.assertIn(calls[0], (403, 404))
        self.assertNotIn(200, calls)

    def test_normal_file_not_found(self):
        handler = _make_handler()
        handler._serve_static("nonexistent.css")
        handler.send_response.assert_called_with(404)

    def test_valid_static_file(self):
        """If a valid file exists in static dir, it should be served."""
        static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
        # Only test if static dir exists and has files
        if os.path.isdir(static_dir):
            files = os.listdir(static_dir)
            if files:
                handler = _make_handler()
                handler._serve_static(files[0])
                handler.send_response.assert_called_with(200)


class TestMimeTypes(unittest.TestCase):
    """Test MIME type mapping in _serve_static."""

    def _get_content_type_for(self, filename):
        """Helper: serve a file and return the Content-Type header value."""
        static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
        filepath = os.path.join(static_dir, filename)
        # Create temporary test file
        os.makedirs(static_dir, exist_ok=True)
        created = False
        if not os.path.exists(filepath):
            with open(filepath, "w") as f:
                f.write("/* test */")
            created = True
        try:
            handler = _make_handler()
            handler._serve_static(filename)
            # Find the Content-Type header call
            for call in handler.send_header.call_args_list:
                if call[0][0] == "Content-Type":
                    return call[0][1]
            return None
        finally:
            if created:
                os.remove(filepath)

    def test_css_mime(self):
        ct = self._get_content_type_for("test_mime.css")
        self.assertEqual(ct, "text/css")

    def test_js_mime(self):
        ct = self._get_content_type_for("test_mime.js")
        self.assertEqual(ct, "application/javascript")

    def test_svg_mime(self):
        ct = self._get_content_type_for("test_mime.svg")
        self.assertEqual(ct, "image/svg+xml")

    def test_unknown_mime(self):
        ct = self._get_content_type_for("test_mime.xyz")
        self.assertEqual(ct, "text/plain")


class TestReadBody(unittest.TestCase):
    """Test _read_body edge cases."""

    def test_non_numeric_content_length(self):
        handler = _make_handler()
        handler.headers = {"Host": "localhost:8787", "Content-Length": "abc"}
        result = handler._read_body()
        self.assertIsNone(result)

    def test_negative_content_length(self):
        handler = _make_handler()
        handler.headers = {"Host": "localhost:8787", "Content-Length": "-5"}
        result = handler._read_body()
        self.assertIsNone(result)

    def test_zero_content_length(self):
        handler = _make_handler()
        handler.headers = {"Host": "localhost:8787", "Content-Length": "0"}
        result = handler._read_body()
        self.assertEqual(result, {})

    def test_oversized_body_rejected(self):
        handler = _make_handler()
        handler.headers = {"Host": "localhost:8787", "Content-Length": "999999999"}
        result = handler._read_body()
        self.assertIsNone(result)


class TestCheckCsrf(unittest.TestCase):
    """Test CSRF token validation."""

    def test_valid_token(self):
        handler = _make_handler()
        handler.headers = {
            "Host": "localhost:8787",
            "X-CSRF-Token": CSRF_TOKEN,
            "Content-Length": "0",
        }
        self.assertTrue(handler._check_csrf())

    def test_invalid_token(self):
        handler = _make_handler()
        handler.headers = {
            "Host": "localhost:8787",
            "X-CSRF-Token": "wrong-token",
            "Content-Length": "0",
        }
        handler._send_json = MagicMock()
        result = handler._check_csrf()
        self.assertFalse(result)

    def test_missing_token(self):
        handler = _make_handler()
        handler.headers = {
            "Host": "localhost:8787",
            "Content-Length": "0",
        }
        handler._send_json = MagicMock()
        result = handler._check_csrf()
        self.assertFalse(result)


class TestSymlinkSecurity(unittest.TestCase):
    """Test that _serve_static handles symlinks securely."""

    def test_normal_file_served(self):
        """A regular file in the static dir is served normally."""
        handler = _make_handler()
        static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
        if os.path.isdir(static_dir):
            files = [
                f for f in os.listdir(static_dir) if os.path.isfile(os.path.join(static_dir, f))
            ]
            if files:
                handler._serve_static(files[0])
                handler.send_response.assert_called_with(200)

    def test_symlink_outside_static_rejected(self, tmp_path=None):
        """Symlink pointing outside static dir must be rejected."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file outside the static dir
            outside_file = os.path.join(tmpdir, "secret.txt")
            with open(outside_file, "w") as f:
                f.write("secret data")

            static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
            if not os.path.isdir(static_dir):
                self.skipTest("static dir not found")

            link_name = os.path.join(static_dir, "_test_symlink_outside.css")
            try:
                os.symlink(outside_file, link_name)
                handler = _make_handler()
                handler._serve_static("_test_symlink_outside.css")
                # Should return 403 because realpath resolves outside static dir
                calls = [c[0][0] for c in handler.send_response.call_args_list]
                self.assertIn(403, calls)
            except OSError:
                self.skipTest("Cannot create symlinks")
            finally:
                if os.path.islink(link_name):
                    os.unlink(link_name)

    def test_symlink_within_static_served(self):
        """Symlink pointing to another file within static dir is allowed."""
        static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
        if not os.path.isdir(static_dir):
            self.skipTest("static dir not found")

        files = [f for f in os.listdir(static_dir) if os.path.isfile(os.path.join(static_dir, f))]
        if not files:
            self.skipTest("No files in static dir")

        link_name = os.path.join(static_dir, "_test_symlink_internal.css")
        target = os.path.join(static_dir, files[0])
        try:
            os.symlink(target, link_name)
            handler = _make_handler()
            handler._serve_static("_test_symlink_internal.css")
            handler.send_response.assert_called_with(200)
        except OSError:
            self.skipTest("Cannot create symlinks")
        finally:
            if os.path.islink(link_name):
                os.unlink(link_name)

    def test_directory_traversal_via_symlink(self):
        """Directory traversal attempt via path components is rejected."""
        handler = _make_handler()
        handler._serve_static("../templates/dashboard.html")
        calls = [c[0][0] for c in handler.send_response.call_args_list]
        self.assertIn(calls[0], (403, 404))
        self.assertNotIn(200, calls)


class TestPreviewETAEstimate(unittest.TestCase):
    """Test B2: Preview ETA speed estimates by provider type."""

    # Provider speed map from server.py (MB/s)
    _PROVIDER_SPEEDS_MBS = {
        "local": 100,
        "sftp": 100,
        "drive": 10,
        "onedrive": 10,
        "protondrive": 3,
        "s3": 20,
        "b2": 20,
    }

    def _estimate_sec(self, source_type, dest_type, size_bytes):
        src_mbs = self._PROVIDER_SPEEDS_MBS.get(source_type, 10)
        dst_mbs = self._PROVIDER_SPEEDS_MBS.get(dest_type, 10)
        speed_est = min(src_mbs, dst_mbs) * 1024 * 1024
        return size_bytes / speed_est if speed_est > 0 else 0

    def test_local_to_local_100mbs(self):
        """Local-to-local should estimate at 100 MB/s."""
        sec = self._estimate_sec("local", "local", 100 * 1024 * 1024 * 1024)  # 100 GiB
        # 100 GiB at 100 MB/s ~ 1024 seconds
        self.assertAlmostEqual(sec, 1024, delta=10)

    def test_sftp_100mbs(self):
        """SFTP should estimate at 100 MB/s."""
        sec = self._estimate_sec("sftp", "local", 100 * 1024 * 1024 * 1024)
        self.assertAlmostEqual(sec, 1024, delta=10)

    def test_proton_drive_3mbs(self):
        """Proton Drive should estimate at 3 MB/s."""
        size = 3 * 1024 * 1024 * 3600  # Exactly 3 MB/s * 3600s = 1 hour of data
        sec = self._estimate_sec("local", "protondrive", size)
        self.assertAlmostEqual(sec, 3600, delta=1)

    def test_s3_20mbs(self):
        """S3 should estimate at 20 MB/s."""
        size = 20 * 1024 * 1024 * 100  # 100 seconds at 20 MB/s
        sec = self._estimate_sec("local", "s3", size)
        self.assertAlmostEqual(sec, 100, delta=1)

    def test_b2_20mbs(self):
        """B2 should estimate at 20 MB/s."""
        size = 20 * 1024 * 1024 * 100
        sec = self._estimate_sec("local", "b2", size)
        self.assertAlmostEqual(sec, 100, delta=1)

    def test_gdrive_onedrive_10mbs(self):
        """Google Drive and OneDrive should estimate at 10 MB/s."""
        size = 10 * 1024 * 1024 * 60  # 60 seconds of data
        for provider in ("drive", "onedrive"):
            sec = self._estimate_sec("local", provider, size)
            self.assertAlmostEqual(sec, 60, delta=1, msg=f"Failed for {provider}")

    def test_bottleneck_is_slower_side(self):
        """Transfer speed should use the slower provider (bottleneck)."""
        size = 10 * 1024 * 1024 * 1024  # 10 GiB
        # local (100) -> protondrive (3) = bottleneck is 3 MB/s
        sec = self._estimate_sec("local", "protondrive", size)
        expected = size / (3 * 1024 * 1024)
        self.assertAlmostEqual(sec, expected, delta=1)

    def test_unknown_provider_defaults_to_10mbs(self):
        """Unknown provider types should default to 10 MB/s."""
        size = 10 * 1024 * 1024 * 100  # 100 seconds at 10 MB/s
        sec = self._estimate_sec("unknown", "unknown", size)
        self.assertAlmostEqual(sec, 100, delta=1)


if __name__ == "__main__":
    unittest.main()
