"""Stress tests for CloudHop thread safety and concurrency.

These tests use real threads (not mocks) with timeouts to verify that
concurrent operations on TransferManager do not deadlock, corrupt state, or
crash.  Timeout is 15s to accommodate slow CI runners.
"""

import json
import threading
import time
from typing import List
from unittest.mock import MagicMock, patch

from cloudhop.transfer import TransferManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIMEOUT_SEC = 15


def _run_with_timeout(fn, timeout=TIMEOUT_SEC):
    """Run *fn* in a thread; raise if it doesn't finish within *timeout* seconds."""
    result = [None]
    exc = [None]

    def wrapper():
        try:
            result[0] = fn()
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=wrapper, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"{fn.__name__} did not complete within {timeout}s (possible deadlock)")
    if exc[0] is not None:
        raise exc[0]
    return result[0]


def _write_fake_log(path: str, sessions: int = 1, lines_per_session: int = 20) -> None:
    """Write a multi-session fake rclone log."""
    with open(path, "w") as f:
        for s in range(sessions):
            base_elapsed = 600 * s
            for i in range(lines_per_session):
                elapsed = base_elapsed + i * 30
                h = elapsed // 3600
                m = (elapsed % 3600) // 60
                sec = elapsed % 60
                ts = f"2025/06/10 {10 + s:02d}:{m:02d}:{sec:02d}"
                transferred_mib = (s * lines_per_session + i) * 50
                total_mib = sessions * lines_per_session * 50
                pct = min(int(transferred_mib / total_mib * 100), 99) if total_mib else 0
                f.write(f"{ts} INFO  :\n")
                f.write(
                    f"Transferred:   \t  {transferred_mib} MiB / {total_mib} MiB, "
                    f"{pct}%, 50.000 MiB/s, ETA 30s\n"
                )
                files_done = s * lines_per_session + i
                f.write(f"Transferred:           {files_done} / 100, {files_done}%\n")
                f.write("Errors:                 0\n")
                f.write(f"Elapsed time:      {h}h{m}m{sec}.0s\n")
                if i % 5 == 0:
                    f.write(f"{ts} INFO  : file_{s}_{i}.dat: Copied (new)\n")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConcurrentScanAndParse:
    """Background scanner + dashboard poll should not deadlock or corrupt state."""

    def test_concurrent_scan_and_parse_current(self, tmp_path):
        """20 threads: 10 call scan_full_log, 10 call parse_current."""
        mgr = TransferManager(cm_dir=str(tmp_path))
        mgr.log_file = str(tmp_path / "stress.log")
        _write_fake_log(mgr.log_file, sessions=2, lines_per_session=30)

        errors: List[str] = []
        barrier = threading.Barrier(20, timeout=TIMEOUT_SEC)

        def scanner():
            try:
                barrier.wait()
                for _ in range(5):
                    mgr.scan_full_log()
            except Exception as e:
                errors.append(f"scanner: {e}")

        def poller():
            try:
                barrier.wait()
                for _ in range(10):
                    result = mgr.parse_current()
                    assert isinstance(result, dict)
            except Exception as e:
                errors.append(f"poller: {e}")

        threads = [threading.Thread(target=scanner) for _ in range(10)]
        threads += [threading.Thread(target=poller) for _ in range(10)]

        def run():
            for t in threads:
                t.start()
            for t in threads:
                t.join(TIMEOUT_SEC)
            alive = [t for t in threads if t.is_alive()]
            if alive:
                raise TimeoutError(f"{len(alive)} threads still alive (deadlock?)")
            if errors:
                raise AssertionError(f"Thread errors: {errors}")

        _run_with_timeout(run)

    def test_scan_with_log_truncation(self, tmp_path):
        """Scanner handles log truncation mid-scan."""
        mgr = TransferManager(cm_dir=str(tmp_path))
        mgr.log_file = str(tmp_path / "trunc.log")
        _write_fake_log(mgr.log_file, sessions=1, lines_per_session=20)

        # First scan to establish offset
        mgr.scan_full_log()
        assert mgr.state.get("last_scan_offset", 0) > 0

        # Truncate the log (simulates log rotation)
        with open(mgr.log_file, "w") as f:
            f.write("2025/06/10 12:00:00 INFO  :\n")
            f.write("Transferred:   \t  10 MiB / 100 MiB, 10%, 5.000 MiB/s, ETA 60s\n")
            f.write("Transferred:            1 / 10, 10%\n")
            f.write("Errors:                 0\n")
            f.write("Elapsed time:      30.0s\n")

        # Second scan should detect truncation and re-scan from start
        mgr.scan_full_log()
        assert mgr.state.get("sessions") is not None


class TestConcurrentPauseResume:
    """Concurrent pause/resume must not deadlock or corrupt state."""

    def test_rapid_pause_resume_cycle(self, tmp_path):
        """5 threads rapidly pause/resume; no deadlock or crash."""
        mgr = TransferManager(cm_dir=str(tmp_path))
        mgr.log_file = str(tmp_path / "pause.log")
        _write_fake_log(mgr.log_file, sessions=1)
        mgr.rclone_cmd = ["sleep", "60"]

        errors: List[str] = []
        barrier = threading.Barrier(5, timeout=TIMEOUT_SEC)

        def cycle():
            try:
                barrier.wait()
                for _ in range(10):
                    mgr.pause()
                    mgr.resume()
            except Exception as e:
                errors.append(str(e))

        def run():
            threads = [threading.Thread(target=cycle) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(TIMEOUT_SEC)
            alive = [t for t in threads if t.is_alive()]
            if alive:
                raise TimeoutError(f"{len(alive)} threads deadlocked in pause/resume")
            if errors:
                raise AssertionError(f"Errors: {errors}")

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 9999
            mock_proc.poll.return_value = None  # always "running"
            mock_proc.terminate.return_value = None
            mock_popen.return_value = mock_proc
            _run_with_timeout(run)


class TestConcurrentQueueOperations:
    """Queue add/remove/process under concurrency."""

    def test_concurrent_queue_add_remove(self, tmp_path):
        """10 threads add, 5 threads remove; no crash or data loss."""
        mgr = TransferManager(cm_dir=str(tmp_path))
        errors: List[str] = []
        barrier = threading.Barrier(15, timeout=TIMEOUT_SEC)

        def adder(idx):
            try:
                barrier.wait()
                for i in range(5):
                    mgr.queue_add({"source": f"src{idx}_{i}:", "dest": f"dst{idx}_{i}:"})
            except Exception as e:
                errors.append(f"adder {idx}: {e}")

        def remover():
            try:
                barrier.wait()
                for _ in range(5):
                    mgr.queue_remove(0)
                    time.sleep(0.01)
            except Exception as e:
                errors.append(f"remover: {e}")

        def run():
            threads = [threading.Thread(target=adder, args=(i,)) for i in range(10)]
            threads += [threading.Thread(target=remover) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(TIMEOUT_SEC)
            alive = [t for t in threads if t.is_alive()]
            if alive:
                raise TimeoutError(f"{len(alive)} threads stuck in queue ops")
            if errors:
                raise AssertionError(f"Errors: {errors}")

        _run_with_timeout(run)
        # Queue should be consistent (no crashes, file on disk is valid JSON)
        mgr._load_queue()
        assert isinstance(mgr.queue, list)


class TestConcurrentStateAccess:
    """State read/write under concurrency."""

    def test_concurrent_save_load_state(self, tmp_path):
        """10 threads save state, 10 threads load state simultaneously."""
        mgr = TransferManager(cm_dir=str(tmp_path))
        errors: List[str] = []
        barrier = threading.Barrier(20, timeout=TIMEOUT_SEC)

        def saver(idx):
            try:
                barrier.wait()
                for i in range(10):
                    with mgr.state_lock:
                        mgr.state["cumulative_transferred_bytes"] = idx * 10 + i
                    mgr.save_state()
            except Exception as e:
                errors.append(f"saver {idx}: {e}")

        def loader():
            try:
                barrier.wait()
                for _ in range(10):
                    loaded = mgr.load_state()
                    assert isinstance(loaded, dict)
                    assert "sessions" in loaded
            except Exception as e:
                errors.append(f"loader: {e}")

        def run():
            threads = [threading.Thread(target=saver, args=(i,)) for i in range(10)]
            threads += [threading.Thread(target=loader) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(TIMEOUT_SEC)
            alive = [t for t in threads if t.is_alive()]
            if alive:
                raise TimeoutError(f"{len(alive)} threads stuck in state I/O")
            if errors:
                raise AssertionError(f"Errors: {errors}")

        _run_with_timeout(run)
        # State file should be valid JSON
        with open(mgr.state_file) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_set_transfer_paths_under_contention(self, tmp_path):
        """Multiple threads calling set_transfer_paths concurrently."""
        mgr = TransferManager(cm_dir=str(tmp_path))
        errors: List[str] = []
        barrier = threading.Barrier(10, timeout=TIMEOUT_SEC)

        def setter(idx):
            try:
                barrier.wait()
                for i in range(5):
                    mgr.set_transfer_paths(f"remote{idx}_{i}:", f"dst{idx}_{i}:")
            except Exception as e:
                errors.append(f"setter {idx}: {e}")

        def run():
            threads = [threading.Thread(target=setter, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(TIMEOUT_SEC)
            alive = [t for t in threads if t.is_alive()]
            if alive:
                raise TimeoutError(f"{len(alive)} threads stuck")
            if errors:
                raise AssertionError(f"Errors: {errors}")

        _run_with_timeout(run)


class TestMixedConcurrentWorkload:
    """Simulate real-world scenario: scanner + polls + user actions."""

    def test_scanner_poll_pause_queue_simultaneous(self, tmp_path):
        """All operations at once: scan + parse_current + pause + queue_add."""
        mgr = TransferManager(cm_dir=str(tmp_path))
        mgr.log_file = str(tmp_path / "mixed.log")
        _write_fake_log(mgr.log_file, sessions=2, lines_per_session=15)
        mgr.rclone_cmd = ["sleep", "60"]

        errors: List[str] = []
        stop = threading.Event()

        def scanner():
            try:
                while not stop.is_set():
                    mgr.scan_full_log()
                    time.sleep(0.05)
            except Exception as e:
                errors.append(f"scanner: {e}")

        def poller():
            try:
                while not stop.is_set():
                    mgr.parse_current()
                    time.sleep(0.02)
            except Exception as e:
                errors.append(f"poller: {e}")

        def pauser():
            try:
                while not stop.is_set():
                    mgr.pause()
                    time.sleep(0.05)
                    mgr.resume()
                    time.sleep(0.05)
            except Exception as e:
                errors.append(f"pauser: {e}")

        def queuer():
            try:
                for i in range(10):
                    mgr.queue_add({"source": f"s{i}:", "dest": f"d{i}:"})
                    time.sleep(0.02)
            except Exception as e:
                errors.append(f"queuer: {e}")

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 8888
            mock_proc.poll.return_value = None
            mock_proc.terminate.return_value = None
            mock_popen.return_value = mock_proc

            threads = [
                threading.Thread(target=scanner, daemon=True),
                threading.Thread(target=scanner, daemon=True),
                threading.Thread(target=poller, daemon=True),
                threading.Thread(target=poller, daemon=True),
                threading.Thread(target=poller, daemon=True),
                threading.Thread(target=pauser, daemon=True),
                threading.Thread(target=queuer, daemon=True),
            ]

            def run():
                for t in threads:
                    t.start()
                time.sleep(2)  # let everything run concurrently
                stop.set()
                for t in threads:
                    t.join(TIMEOUT_SEC)
                alive = [t for t in threads if t.is_alive()]
                if alive:
                    raise TimeoutError(f"{len(alive)} threads alive after stop (deadlock?)")
                if errors:
                    raise AssertionError(f"Errors: {errors}")

            _run_with_timeout(run, timeout=TIMEOUT_SEC)


class TestLogEdgeCases:
    """Edge cases in log scanning under stress."""

    def test_log_grows_during_scan(self, tmp_path):
        """Append to log while scanner is reading it."""
        mgr = TransferManager(cm_dir=str(tmp_path))
        mgr.log_file = str(tmp_path / "growing.log")
        _write_fake_log(mgr.log_file, sessions=1, lines_per_session=10)

        errors: List[str] = []
        stop = threading.Event()

        def appender():
            try:
                i = 0
                while not stop.is_set():
                    with open(mgr.log_file, "a") as f:
                        f.write(f"2025/06/10 12:{i:02d}:00 INFO  : extra_{i}.txt: Copied (new)\n")
                    i = (i + 1) % 60
                    time.sleep(0.01)
            except Exception as e:
                errors.append(f"appender: {e}")

        def scanner():
            try:
                while not stop.is_set():
                    mgr.scan_full_log()
                    time.sleep(0.05)
            except Exception as e:
                errors.append(f"scanner: {e}")

        def run():
            threads = [
                threading.Thread(target=appender, daemon=True),
                threading.Thread(target=scanner, daemon=True),
                threading.Thread(target=scanner, daemon=True),
            ]
            for t in threads:
                t.start()
            time.sleep(1.5)
            stop.set()
            for t in threads:
                t.join(TIMEOUT_SEC)
            alive = [t for t in threads if t.is_alive()]
            if alive:
                raise TimeoutError(f"{len(alive)} threads stuck")
            if errors:
                raise AssertionError(f"Errors: {errors}")

        _run_with_timeout(run)
        # State should be valid
        assert mgr.state.get("total_copied_count", 0) > 0

    def test_empty_log_scan_no_crash(self, tmp_path):
        """Scanning an empty log under concurrency."""
        mgr = TransferManager(cm_dir=str(tmp_path))
        mgr.log_file = str(tmp_path / "empty.log")
        with open(mgr.log_file, "w") as f:
            f.write("")

        errors: List[str] = []

        def scanner():
            try:
                for _ in range(20):
                    mgr.scan_full_log()
            except Exception as e:
                errors.append(str(e))

        def run():
            threads = [threading.Thread(target=scanner) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(TIMEOUT_SEC)
            if errors:
                raise AssertionError(f"Errors: {errors}")

        _run_with_timeout(run)


class TestIsRcloneRunningConcurrency:
    """is_rclone_running under contention."""

    def test_concurrent_is_running_checks(self, tmp_path):
        """20 threads calling is_rclone_running simultaneously."""
        mgr = TransferManager(cm_dir=str(tmp_path))
        errors: List[str] = []
        barrier = threading.Barrier(20, timeout=TIMEOUT_SEC)

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 7777
            mock_proc.poll.return_value = None  # "running"
            mock_popen.return_value = mock_proc

            # Start a fake transfer to set _rclone_proc
            mgr.rclone_cmd = ["sleep", "60"]
            mgr.resume()

            def checker():
                try:
                    barrier.wait()
                    for _ in range(50):
                        mgr.is_rclone_running()
                except Exception as e:
                    errors.append(str(e))

            def run():
                threads = [threading.Thread(target=checker) for _ in range(20)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(TIMEOUT_SEC)
                alive = [t for t in threads if t.is_alive()]
                if alive:
                    raise TimeoutError(f"{len(alive)} threads stuck")
                if errors:
                    raise AssertionError(f"Errors: {errors}")

            _run_with_timeout(run)
