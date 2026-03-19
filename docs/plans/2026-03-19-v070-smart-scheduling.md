# CloudHop v0.7.0 - Smart Scheduling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add time-windowed scheduling, bandwidth limiting UI, and macOS notifications so users can run transfers during off-peak hours with speed caps.

**Architecture:** The scheduler lives inside `TransferManager.background_scanner()` as a time-check that auto-pauses/resumes rclone based on schedule config stored in state. The wizard gets a new "Schedule" options section. rclone's native `--bwlimit` flag handles bandwidth. macOS notifications use `osascript` (zero dependencies).

**Tech Stack:** Python stdlib (threading, datetime, subprocess), rclone `--bwlimit`, HTML/CSS/JS (vanilla), macOS `osascript`

---

## Overview

### Files to touch
- `cloudhop/transfer.py` - Scheduler engine + notification triggers
- `cloudhop/server.py` - New API endpoints for schedule CRUD
- `cloudhop/utils.py` - Schedule constants
- `cloudhop/notify.py` - NEW: macOS/cross-platform notifications
- `cloudhop/templates/wizard.html` - Schedule config UI in options step
- `cloudhop/static/wizard.js` - Schedule form logic
- `cloudhop/static/wizard.css` - Schedule form styles
- `cloudhop/static/dashboard.js` - Show schedule status
- `cloudhop/templates/dashboard.html` - Schedule indicator
- `cloudhop/tests/test_transfer.py` - Scheduler tests
- `cloudhop/tests/test_notify.py` - NEW: notification tests

---

### Task 1: Schedule data model and constants

**Files:**
- Modify: `cloudhop/utils.py`
- Modify: `cloudhop/transfer.py` (TransferManager._default_state)
- Test: `cloudhop/tests/test_transfer.py`

**Step 1: Add schedule constants to utils.py**

Add after the existing constants block:

```python
# Schedule constants
SCHEDULER_CHECK_INTERVAL_SEC = 60  # Check schedule every 60s
```

**Step 2: Add schedule fields to TransferManager._default_state()**

The schedule config stored in state:

```python
# In _default_state(), add to the returned dict:
"schedule": {
    "enabled": False,
    "start_time": "22:00",    # HH:MM - window opens
    "end_time": "06:00",      # HH:MM - window closes
    "days": [0, 1, 2, 3, 4, 5, 6],  # 0=Mon, 6=Sun (all days default)
    "bw_limit_in_window": "",  # e.g. "10M" - unlimited if empty
    "bw_limit_out_window": "0",  # "0" = paused outside window
},
```

**Step 3: Write test for schedule state persistence**

```python
def test_schedule_default_state():
    mgr = TransferManager(cm_dir=tmp_path)
    assert mgr.state["schedule"]["enabled"] is False
    assert mgr.state["schedule"]["start_time"] == "22:00"
    assert mgr.state["schedule"]["end_time"] == "06:00"
    assert len(mgr.state["schedule"]["days"]) == 7
```

**Step 4: Run test, verify pass, commit**

```bash
pytest cloudhop/tests/test_transfer.py::test_schedule_default_state -v
git add -A && git commit -m "feat(schedule): add schedule data model to TransferManager state"
```

---

### Task 2: Schedule engine - time window checker

**Files:**
- Modify: `cloudhop/transfer.py` (new methods + background_scanner integration)
- Test: `cloudhop/tests/test_transfer.py`

**Step 1: Write the time-window check method**

Add to TransferManager:

```python
def is_in_schedule_window(self) -> bool:
    """Check if current time falls within the scheduled transfer window."""
    with self.state_lock:
        schedule = self.state.get("schedule", {})

    if not schedule.get("enabled", False):
        return True  # No schedule = always allowed

    now = datetime.now()
    current_day = now.weekday()  # 0=Monday
    allowed_days = schedule.get("days", [0, 1, 2, 3, 4, 5, 6])

    if current_day not in allowed_days:
        return False

    current_minutes = now.hour * 60 + now.minute
    start_h, start_m = map(int, schedule.get("start_time", "22:00").split(":"))
    end_h, end_m = map(int, schedule.get("end_time", "06:00").split(":"))
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    if start_minutes <= end_minutes:
        # Same-day window (e.g., 09:00 - 17:00)
        return start_minutes <= current_minutes < end_minutes
    else:
        # Overnight window (e.g., 22:00 - 06:00)
        return current_minutes >= start_minutes or current_minutes < end_minutes
```

**Step 2: Write tests for time window logic**

```python
from unittest.mock import patch
from datetime import datetime

def test_schedule_overnight_window_inside(tmp_path):
    mgr = TransferManager(cm_dir=str(tmp_path))
    mgr.state["schedule"] = {
        "enabled": True,
        "start_time": "22:00",
        "end_time": "06:00",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "bw_limit_in_window": "",
        "bw_limit_out_window": "0",
    }
    # 23:30 on a Wednesday should be IN window
    with patch("cloudhop.transfer.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 18, 23, 30)  # Wednesday
        mock_dt.strptime = datetime.strptime
        assert mgr.is_in_schedule_window() is True

def test_schedule_overnight_window_outside(tmp_path):
    mgr = TransferManager(cm_dir=str(tmp_path))
    mgr.state["schedule"] = {
        "enabled": True,
        "start_time": "22:00",
        "end_time": "06:00",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "bw_limit_in_window": "",
        "bw_limit_out_window": "0",
    }
    # 14:00 should be OUTSIDE window
    with patch("cloudhop.transfer.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 18, 14, 0)
        mock_dt.strptime = datetime.strptime
        assert mgr.is_in_schedule_window() is False

def test_schedule_daytime_window(tmp_path):
    mgr = TransferManager(cm_dir=str(tmp_path))
    mgr.state["schedule"] = {
        "enabled": True,
        "start_time": "09:00",
        "end_time": "17:00",
        "days": [0, 1, 2, 3, 4],  # Mon-Fri only
        "bw_limit_in_window": "5M",
        "bw_limit_out_window": "0",
    }
    # 12:00 on Monday = in window
    with patch("cloudhop.transfer.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 16, 12, 0)  # Monday
        mock_dt.strptime = datetime.strptime
        assert mgr.is_in_schedule_window() is True

    # 12:00 on Saturday = wrong day
    with patch("cloudhop.transfer.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 21, 12, 0)  # Saturday
        mock_dt.strptime = datetime.strptime
        assert mgr.is_in_schedule_window() is False

def test_schedule_disabled(tmp_path):
    mgr = TransferManager(cm_dir=str(tmp_path))
    # Default schedule is disabled = always in window
    assert mgr.is_in_schedule_window() is True
```

**Step 3: Run tests, verify pass**

```bash
pytest cloudhop/tests/test_transfer.py -k "schedule" -v
```

**Step 4: Integrate with background_scanner**

Modify `background_scanner()` to check schedule every iteration:

```python
def background_scanner(self) -> None:
    while True:
        try:
            self.scan_full_log()
            self._check_schedule()
        except Exception as e:
            print(f"Scanner error: {e}")
        time.sleep(SCANNER_INTERVAL_SEC)

def _check_schedule(self) -> None:
    """Auto-pause/resume based on schedule window."""
    with self.state_lock:
        schedule = self.state.get("schedule", {})
    if not schedule.get("enabled", False):
        return

    in_window = self.is_in_schedule_window()

    if in_window and not self.is_rclone_running() and self.rclone_cmd:
        # Window opened - resume transfer
        result = self.resume()
        if result.get("ok"):
            from .notify import notify
            notify("CloudHop", "Transfer resumed (schedule window opened)")

    elif not in_window and self.is_rclone_running():
        # Window closed - pause transfer
        result = self.pause()
        if result.get("ok"):
            from .notify import notify
            notify("CloudHop", "Transfer paused (outside schedule window)")
```

**Step 5: Commit**

```bash
git add -A && git commit -m "feat(schedule): add time window checker with auto-pause/resume"
```

---

### Task 3: macOS notifications

**Files:**
- Create: `cloudhop/notify.py`
- Test: `cloudhop/tests/test_notify.py`

**Step 1: Create notify.py**

```python
"""Cross-platform desktop notifications for CloudHop."""

import platform
import subprocess


def notify(title: str, message: str) -> None:
    """Send a desktop notification. Fails silently."""
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.run(
                [
                    "osascript", "-e",
                    f'display notification "{message}" with title "{title}"'
                ],
                capture_output=True,
                timeout=5,
            )
        elif system == "Linux":
            subprocess.run(
                ["notify-send", title, message],
                capture_output=True,
                timeout=5,
            )
        # Windows: toast notifications require pywin32 or plyer - skip for now
    except Exception:
        pass
```

**Step 2: Add notification triggers in TransferManager**

Add to `parse_current()` - detect when transfer finishes:

```python
# At the end of parse_current(), after setting result["finished"]:
if result["finished"] and not self._notified_complete:
    self._notified_complete = True
    from .notify import notify
    pct = result.get("global_pct", 0)
    notify("CloudHop", f"Transfer complete! ({pct}%)")
```

Add `self._notified_complete = False` to `__init__`.

**Step 3: Write test**

```python
from unittest.mock import patch
from cloudhop.notify import notify

def test_notify_darwin(monkeypatch):
    monkeypatch.setattr("cloudhop.notify.platform.system", lambda: "Darwin")
    with patch("cloudhop.notify.subprocess.run") as mock_run:
        notify("Test", "Hello")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"

def test_notify_fails_silently():
    with patch("cloudhop.notify.subprocess.run", side_effect=Exception("nope")):
        notify("Test", "Hello")  # Should not raise
```

**Step 4: Run tests, commit**

```bash
pytest cloudhop/tests/test_notify.py -v
git add -A && git commit -m "feat(notify): add cross-platform desktop notifications"
```

---

### Task 4: Schedule API endpoints

**Files:**
- Modify: `cloudhop/server.py`

**Step 1: Add GET /api/schedule**

```python
elif self.path == "/api/schedule":
    with self.manager.state_lock:
        schedule = self.manager.state.get("schedule", {})
    schedule["in_window"] = self.manager.is_in_schedule_window()
    self._send_json(schedule)
```

**Step 2: Add POST /api/schedule**

```python
elif self.path == "/api/schedule":
    body = self._read_body()
    if body is None:
        self._send_json({"ok": False, "msg": "Invalid request"}, 400)
        return
    # Validate
    start_time = body.get("start_time", "22:00")
    end_time = body.get("end_time", "06:00")
    # Basic HH:MM validation
    import re
    time_re = re.compile(r"^\d{2}:\d{2}$")
    if not time_re.match(start_time) or not time_re.match(end_time):
        self._send_json({"ok": False, "msg": "Invalid time format"}, 400)
        return
    days = body.get("days", [0, 1, 2, 3, 4, 5, 6])
    if not isinstance(days, list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
        self._send_json({"ok": False, "msg": "Invalid days"}, 400)
        return

    with self.manager.state_lock:
        self.manager.state["schedule"] = {
            "enabled": bool(body.get("enabled", False)),
            "start_time": start_time,
            "end_time": end_time,
            "days": days,
            "bw_limit_in_window": body.get("bw_limit_in_window", ""),
            "bw_limit_out_window": body.get("bw_limit_out_window", "0"),
        }
        self.manager.save_state()
    self._send_json({"ok": True})
```

**Step 3: Commit**

```bash
git add -A && git commit -m "feat(schedule): add GET/POST /api/schedule endpoints"
```

---

### Task 5: Wizard UI - Schedule options

**Files:**
- Modify: `cloudhop/templates/wizard.html` (options step)
- Modify: `cloudhop/static/wizard.js`
- Modify: `cloudhop/static/wizard.css`

**Step 1: Add schedule section to wizard options step**

In the wizard's options/confirm step (step 5), add a collapsible "Schedule" section:

```html
<div class="option-section">
  <h3>Schedule (optional)</h3>
  <label class="toggle-row">
    <input type="checkbox" id="scheduleEnabled">
    <span>Only transfer during specific hours</span>
  </label>
  <div id="scheduleConfig" style="display:none;">
    <div class="time-row">
      <label>From <input type="time" id="scheduleStart" value="22:00"></label>
      <label>To <input type="time" id="scheduleEnd" value="06:00"></label>
    </div>
    <div class="days-row">
      <label><input type="checkbox" data-day="0" checked> Mon</label>
      <label><input type="checkbox" data-day="1" checked> Tue</label>
      <label><input type="checkbox" data-day="2" checked> Wed</label>
      <label><input type="checkbox" data-day="3" checked> Thu</label>
      <label><input type="checkbox" data-day="4" checked> Fri</label>
      <label><input type="checkbox" data-day="5" checked> Sat</label>
      <label><input type="checkbox" data-day="6" checked> Sun</label>
    </div>
    <div class="bw-row">
      <label>Speed limit during window
        <select id="bwLimitIn">
          <option value="">Unlimited</option>
          <option value="1M">1 MB/s</option>
          <option value="2M">2 MB/s</option>
          <option value="5M">5 MB/s</option>
          <option value="10M">10 MB/s</option>
          <option value="20M">20 MB/s</option>
          <option value="50M">50 MB/s</option>
        </select>
      </label>
    </div>
  </div>
</div>
```

**Step 2: Add JS to toggle schedule and send config**

```javascript
document.getElementById('scheduleEnabled').addEventListener('change', function() {
    document.getElementById('scheduleConfig').style.display = this.checked ? 'block' : 'none';
});
```

When the wizard starts the transfer, also POST the schedule config to `/api/schedule`.

**Step 3: Commit**

```bash
git add -A && git commit -m "feat(schedule): add schedule UI to wizard options step"
```

---

### Task 6: Dashboard - Schedule status indicator

**Files:**
- Modify: `cloudhop/static/dashboard.js`
- Modify: `cloudhop/templates/dashboard.html`

**Step 1: Poll schedule status**

In the dashboard's status poll loop, also fetch `/api/schedule` and display:
- "Scheduled: 22:00 - 06:00" badge
- "In window" / "Paused (outside window)" indicator
- Next window open/close time

**Step 2: Add schedule indicator to dashboard header**

```html
<div class="schedule-badge" id="scheduleBadge" style="display:none;">
  <span class="schedule-dot"></span>
  <span id="scheduleText">Scheduled</span>
</div>
```

**Step 3: Commit**

```bash
git add -A && git commit -m "feat(schedule): show schedule status on dashboard"
```

---

### Task 7: Bandwidth limiter with rclone rc

**Files:**
- Modify: `cloudhop/transfer.py`

**Note:** rclone supports live bandwidth changes via `--rc` flag + `rclone rc core/bwlimit rate=10M`. This means we can change bandwidth without restarting rclone.

**Step 1: Add --rc flag to rclone command in start_transfer**

In `_start_transfer_locked()`, add `--rc` to the rclone command.

**Step 2: Add method to change bandwidth live**

```python
def set_bandwidth(self, limit: str) -> Dict[str, Any]:
    """Change rclone bandwidth limit on the fly via rc API."""
    if not self.is_rclone_running():
        return {"ok": False, "msg": "rclone not running"}
    try:
        result = subprocess.run(
            ["rclone", "rc", "core/bwlimit", f"rate={limit}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "msg": result.stderr}
    except Exception as e:
        return {"ok": False, "msg": str(e)}
```

**Step 3: Use in _check_schedule for per-window bandwidth**

Instead of pause/resume at window boundaries, change bandwidth:
- In window: set configured speed (or unlimited)
- Outside window: set to "0" (effectively paused) OR a lower speed

**Step 4: Commit**

```bash
git add -A && git commit -m "feat(bwlimit): live bandwidth control via rclone rc"
```

---

### Task 8: Integration test and version bump

**Files:**
- Modify: `pyproject.toml` (version 0.7.0)
- Modify: `CHANGELOG.md`

**Step 1: Run full test suite**

```bash
pytest cloudhop/tests/ -v
```

**Step 2: Update version to 0.7.0**

**Step 3: Update CHANGELOG.md**

**Step 4: Final commit and tag**

```bash
git add -A && git commit -m "release: CloudHop v0.7.0 - Smart Scheduling"
git tag v0.7.0
```

---

## Execution order

Tasks 1-3 are independent and can be parallelized.
Task 4 depends on Task 1.
Task 5 depends on Task 4.
Task 6 depends on Task 4.
Task 7 depends on Task 2.
Task 8 depends on all.

```
[Task 1: Data model] ──> [Task 4: API] ──> [Task 5: Wizard UI]
                                        ──> [Task 6: Dashboard UI]
[Task 2: Engine]     ──> [Task 7: Bandwidth]
[Task 3: Notify]     ──────────────────────> [Task 8: Release]
```
