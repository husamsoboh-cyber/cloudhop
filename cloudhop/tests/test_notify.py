"""Tests for cloudhop.notify."""

from unittest.mock import patch

from cloudhop.notify import notify


def test_notify_darwin():
    with patch("cloudhop.notify.platform.system", return_value="Darwin"):
        with patch("cloudhop.notify.subprocess.run") as mock_run:
            notify("Test Title", "Test message")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "osascript"
            assert "Test message" in args[2]
            assert "Test Title" in args[2]


def test_notify_linux():
    with patch("cloudhop.notify.platform.system", return_value="Linux"):
        with patch("cloudhop.notify.subprocess.run") as mock_run:
            notify("Test", "Hello")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "notify-send"


def test_notify_windows_noop():
    with patch("cloudhop.notify.platform.system", return_value="Windows"):
        with patch("cloudhop.notify.subprocess.run") as mock_run:
            notify("Test", "Hello")
            mock_run.assert_not_called()


def test_notify_fails_silently():
    with patch("cloudhop.notify.platform.system", return_value="Darwin"):
        with patch("cloudhop.notify.subprocess.run", side_effect=Exception("fail")):
            notify("Test", "Hello")  # Must not raise
