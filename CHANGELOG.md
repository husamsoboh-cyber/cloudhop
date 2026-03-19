# Changelog

All notable changes to CloudMirror are documented here.

## v0.4.0 (2026-03-19)

### Security
- CSRF protection with double-submit token pattern (SameSite=Strict cookie + X-CSRF-Token header)
- DNS rebinding protection via Host header validation
- Timing-safe CSRF comparison using `hmac.compare_digest()`
- Input validation on all API endpoints including `/api/wizard/preview`
- S3, MEGA, and Proton Drive credentials passed via environment variables (no longer visible in `ps aux`)
- Stricter exclude pattern validation (rejects glob injection characters `{}[]`)
- Transfer lock prevents TOCTOU race conditions on start/pause/resume
- XSS protection on file type extensions, preview results, and confirm modals
- State file credential filtering before persisting to disk

### Performance
- Incremental log scanning with byte offset tracking (no longer reads entire log every 30s)
- Chart history cached in state (dashboard no longer re-parses full log on every 5s poll)
- Pre-compiled regexes at module level for all hot-path parsing
- Named constants replace magic numbers throughout
- Extracted `downsample()`, `_parse_tail_stats()`, `_parse_active_transfers()`, `_parse_recent_files()`, `_parse_error_messages()` from monolithic functions
- Capped history lists at 50,000 entries to prevent unbounded memory growth
- Stats interval changed from 30s to 10s for more responsive dashboard

### Added
- Same-provider transfers (e.g., Google Drive to Google Drive with two accounts)
- Checksum verification option in wizard ("Verify with checksums" checkbox)
- Connection-lost banner when server becomes unreachable
- Graceful SIGTERM handler (transfer continues in background)
- Port auto-retry (tries ports 8787-8791 if default is busy)
- System dark/light mode auto-detection via `prefers-color-scheme`
- Live system theme change listener (follows OS dark mode toggle)
- Styled confirm/error modals replacing native `alert()`/`confirm()`

### Accessibility
- `role="dialog"` and `aria-modal="true"` on all modals
- Escape key closes modals
- Focus trap and stacking guard on modals
- `role="alert"` and `aria-live="assertive"` on connection-lost banner
- `role="status"` and `aria-live="polite"` on toast notifications
- `role="progressbar"` with dynamically updated `aria-valuenow` on progress bar
- `role="radiogroup"` and `role="radio"` with `aria-checked` on speed options
- `*:focus-visible` outline styles for keyboard navigation
- Improved color contrast (WCAG AA compliant) for `--text-muted`, `--text-dim`, `--chart-text`
- `aria-label` on theme toggles and chart SVGs

### Fixed
- Same-provider transfers now create separate rclone remotes (e.g., `gdrive` + `gdrive_dest`)
- Port retry now correctly updates global PORT (CORS origins, browser URL, printed URL all match)
- Incremental log scanner handles partial lines at seek boundaries
- `pause_rclone()` now acquires transfer lock (prevents race with resume)
- Event listener leak on "Other" provider input selection
- Hardcoded `ro-RO` locale replaced with browser default
- History modal no longer stacks on repeated clicks
- Theme toggle icons consistent between dashboard and wizard (unicode, not emoji)
- Checksum option shown in transfer summary before starting
- Connection-lost banner adjusts page padding on mobile
- State file validates `_running_*` types on load to prevent corruption

## v0.3.0 (2026-03-19)

### Added
- Progress percentage in browser tab title (`[45%] CloudMirror`)
- Bandwidth limit option in wizard (e.g. 10M, 1G, 500K)
- Dry-run / Preview button on summary step (shows file count and size before starting)
- Transfer history API (`/api/history`) with link in dashboard footer
- Smoothed ETA calculation based on average speed (less fluctuation)
- Error message mapping (translates rclone errors to user-friendly messages)
- OAuth reconnect hint when token-related errors appear
- Chart cache invalidation on theme toggle (charts redraw with correct colors)

## v0.2.0 (2026-03-18)

### Added
- OAuth hint conditional (different message for browser auth vs credentials)
- Rclone installation check on Welcome page (warns before user proceeds)
- Wizard state persistence via sessionStorage (survives page refresh)
- Cancel Transfer button on dashboard (with confirmation dialog)
- Dead process detection (badge shows "Stopped" in red when rclone dies)
- Quick-select buttons for local folder paths (Desktop, Documents, Downloads)
- Auto-create destination folder if it doesn't exist

### Fixed
- Proton/MEGA/S3 credentials now save correctly (key=value format)
- Proton Drive label no longer shows as "Google Drive"
- Quick-select buttons use actual home directory (not localhost fallback)
- Quick-select buttons meet 44px touch target on mobile
- Zombie rclone size processes prevented (only one at a time)
- Dead rclone process properly detected (waitpid instead of kill)
- Empty local path shows alert instead of defaulting to "/" (read-only)

## v0.1.0 (2026-03-18)

### Features
- Web-based 6-step setup wizard with provider selection
- Real-time transfer dashboard with live-updating charts
- Supports Google Drive, OneDrive, Dropbox, MEGA, Amazon S3, Proton Drive, Local Folder
- Dark/light theme toggle with persistence
- Pause/Resume/Cancel transfers
- Session history tracking with downtime detection
- Speed, progress, and file count history charts
- Active transfer list and recent files feed
- File type breakdown
- Error tracking and display
- CLI mode for direct rclone argument passthrough
- Automatic rclone installation (macOS and Linux)
- Sound and desktop notification on transfer completion
- Single Python file, no dependencies beyond Python 3.7+

### Security
- Web server binds to localhost only (127.0.0.1)
- CORS restricted to localhost origins (strict set comparison)
- Input validation prevents rclone flag injection
- Request body size limited to 10 KB
- Credentials obscured before saving
- State files stored in ~/.cloudmirror/ with 0700 permissions

### Accessibility
- Provider cards keyboard-accessible (tabindex, role, onkeydown)
- Form labels linked to inputs
- Theme toggle and all buttons meet 44px minimum touch target
- Mobile responsive (375px to 1280px)
