"""Tests for cloudhop.cli subcommands."""

import builtins
import json
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from cloudhop.cli import _cli_subcommand, start_dashboard
from cloudhop.transfer import ensure_rclone, install_rclone


def _mock_api_response(data, cookies=""):
    """Create a mock urlopen response."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(data).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.headers = MagicMock()
    mock_resp.headers.get.return_value = cookies
    return mock_resp


class TestCliStatus:
    @patch("urllib.request.urlopen")
    def test_status_returns_info(self, mock_urlopen, capsys):
        """cloudhop status prints transfer info."""
        mock_urlopen.return_value = _mock_api_response(
            {
                "global_pct": 75.0,
                "global_transferred": "150 GiB",
                "global_total": "200 GiB",
                "speed": "10 MiB/s",
                "eta": "30m",
                "rclone_running": True,
                "errors": 0,
                "global_files_done": 5000,
                "global_files_total": 8000,
            }
        )
        result = _cli_subcommand("status")
        assert result is True
        output = capsys.readouterr().out
        assert "75.0%" in output
        assert "Transferring" in output

    @patch("urllib.request.urlopen", side_effect=Exception("refused"))
    def test_status_server_not_running(self, mock_urlopen, capsys):
        """cloudhop status shows message when server is down."""
        result = _cli_subcommand("status")
        assert result is True
        output = capsys.readouterr().out
        assert "not running" in output

    def test_unknown_command(self):
        """Unknown subcommand returns False."""
        assert _cli_subcommand("unknown") is False


class TestCliPauseResume:
    @patch("urllib.request.urlopen")
    def test_pause_success(self, mock_urlopen, capsys):
        """cloudhop pause prints success message."""
        mock_urlopen.return_value = _mock_api_response(
            {"ok": True, "msg": "Stopped rclone (PID 1234)"},
            cookies="csrf_token=abc123; Path=/",
        )
        result = _cli_subcommand("pause")
        assert result is True
        output = capsys.readouterr().out
        assert "1234" in output

    @patch("urllib.request.urlopen")
    def test_resume_success(self, mock_urlopen, capsys):
        """cloudhop resume prints success message."""
        mock_urlopen.return_value = _mock_api_response(
            {"ok": True, "msg": "Started rclone (PID 5678)"},
            cookies="csrf_token=abc123; Path=/",
        )
        result = _cli_subcommand("resume")
        assert result is True
        output = capsys.readouterr().out
        assert "5678" in output


class TestCliHistory:
    @patch("urllib.request.urlopen")
    def test_history_shows_entries(self, mock_urlopen, capsys):
        """cloudhop history lists transfers."""
        mock_urlopen.return_value = _mock_api_response(
            [
                {"label": "OneDrive -> Google Drive", "sessions": 5},
                {"label": "Dropbox -> S3", "sessions": 2},
            ]
        )
        result = _cli_subcommand("history")
        assert result is True
        output = capsys.readouterr().out
        assert "OneDrive" in output
        assert "5 sessions" in output

    @patch("urllib.request.urlopen")
    def test_history_empty(self, mock_urlopen, capsys):
        """cloudhop history shows message when empty."""
        mock_urlopen.return_value = _mock_api_response([])
        result = _cli_subcommand("history")
        assert result is True
        output = capsys.readouterr().out
        assert "No transfer history" in output


def _make_manager():
    """Create a mock TransferManager for start_dashboard tests."""
    mgr = MagicMock()
    mgr.rclone_cmd = []
    mgr.transfer_active = False
    mgr.cm_dir = "/tmp"
    mgr.state = {}
    return mgr


class TestPyWebViewFallback:
    """Test pywebview import fallback in start_dashboard."""

    @patch("cloudhop.cli.webbrowser")
    @patch("cloudhop.cli.http.server.ThreadingHTTPServer")
    def test_import_error_logs_reason(self, mock_server_cls, mock_wb, caplog):
        """When webview is unavailable, the exact ImportError reason is logged."""
        mock_server = MagicMock()
        mock_server_cls.return_value = mock_server
        mock_server.serve_forever.side_effect = KeyboardInterrupt

        real_import = builtins.__import__

        def fail_webview(name, *args, **kwargs):
            if name == "webview":
                raise ImportError("No module named 'webview'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fail_webview):
            with caplog.at_level(logging.WARNING, logger="cloudhop"):
                with pytest.raises(SystemExit):
                    start_dashboard(_make_manager())

        assert any(
            "pywebview unavailable" in r.message and "No module named" in r.message
            for r in caplog.records
        )

    @patch("cloudhop.cli.webbrowser")
    @patch("cloudhop.cli.http.server.ThreadingHTTPServer")
    def test_import_error_falls_back_to_browser(self, mock_server_cls, mock_wb, capsys):
        """When webview import fails, the tip message is printed."""
        mock_server = MagicMock()
        mock_server_cls.return_value = mock_server
        mock_server.serve_forever.side_effect = KeyboardInterrupt

        real_import = builtins.__import__

        def fail_webview(name, *args, **kwargs):
            if name == "webview":
                raise ImportError("No module named 'webview'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fail_webview):
            with pytest.raises(SystemExit):
                start_dashboard(_make_manager())

        output = capsys.readouterr().out
        assert "pip install pywebview" in output

    @patch("cloudhop.cli.webbrowser")
    @patch("cloudhop.cli.http.server.ThreadingHTTPServer")
    def test_runtime_error_logs_reason(self, mock_server_cls, mock_wb, caplog):
        """When webview raises at runtime, the error reason is logged."""
        mock_webview = MagicMock()
        mock_webview.start.side_effect = RuntimeError("cannot init GUI")

        mock_server = MagicMock()
        mock_server_cls.return_value = mock_server
        mock_server.serve_forever.side_effect = KeyboardInterrupt

        with patch.dict(sys.modules, {"webview": mock_webview}):
            with caplog.at_level(logging.WARNING, logger="cloudhop"):
                with pytest.raises(SystemExit):
                    start_dashboard(_make_manager())

        assert any(
            "pywebview failed" in r.message and "cannot init GUI" in r.message
            for r in caplog.records
        )


class TestInstallRclone:
    @patch("cloudhop.transfer.install_rclone")
    @patch("cloudhop.transfer.find_rclone", return_value="/usr/local/bin/rclone")
    def test_install_rclone_already_exists(self, mock_find, mock_install):
        """ensure_rclone returns path without calling install when rclone exists."""
        result = ensure_rclone()
        assert result == "/usr/local/bin/rclone"
        mock_install.assert_not_called()

    @patch("builtins.input", return_value="y")
    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("platform.system", return_value="darwin")
    @patch("cloudhop.transfer.find_rclone")
    def test_install_rclone_missing_attempts_download(
        self, mock_find, mock_platform, mock_which, mock_run, mock_input
    ):
        """install_rclone uses brew to install on macOS."""
        mock_find.return_value = "/usr/local/bin/rclone"
        mock_which.side_effect = lambda cmd: None if cmd == "rclone" else "/opt/homebrew/bin/brew"
        mock_run.return_value = MagicMock(returncode=0)

        install_rclone()

        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ["brew", "install", "rclone"]

    @patch("builtins.input", return_value="y")
    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("platform.system", return_value="darwin")
    @patch("cloudhop.transfer.find_rclone", return_value=None)
    def test_install_rclone_download_failure(
        self, mock_find, mock_platform, mock_which, mock_run, mock_input
    ):
        """install_rclone calls sys.exit(1) when brew install fails."""
        mock_which.side_effect = lambda cmd: "/opt/homebrew/bin/brew" if cmd == "brew" else None
        mock_run.return_value = MagicMock(returncode=1)

        with pytest.raises(SystemExit) as exc_info:
            install_rclone()
        assert exc_info.value.code == 1
