"""Tests for cloudhop.utils - 100% branch coverage target."""

import pytest

from cloudhop.utils import (
    CHART_DOWNSAMPLE_TARGET,
    LOG_TAIL_BYTES,
    MAX_REQUEST_BODY_BYTES,
    MIN_SESSION_ELAPSED_SEC,
    PORT,
    RE_ACTIVE,
    RE_COPIED,
    RE_COPIED_WITH_TS,
    RE_ELAPSED,
    RE_ERROR_MSG,
    RE_ERRORS,
    RE_SPEED,
    RE_TRANSFERRED_BYTES,
    RE_TRANSFERRED_FILES,
    _sanitize_rclone_error,
    downsample,
    fmt_bytes,
    fmt_duration,
    get_remote_label,
    parse_elapsed,
    to_bytes,
    validate_exclude_pattern,
    validate_rclone_input,
)

# ─── Constants ────────────────────────────────────────────────────────────────


class TestConstants:
    def test_port(self):
        assert PORT == 8787

    def test_log_tail_bytes(self):
        assert LOG_TAIL_BYTES == 16000

    def test_min_session_elapsed(self):
        assert MIN_SESSION_ELAPSED_SEC == 300

    def test_chart_downsample_target(self):
        assert CHART_DOWNSAMPLE_TARGET == 200

    def test_max_request_body_bytes(self):
        assert MAX_REQUEST_BODY_BYTES == 10240


# ─── Regex smoke tests ───────────────────────────────────────────────────────


class TestRegexPatterns:
    def test_transferred_bytes(self):
        line = "Transferred:   90.054 GiB / 120.000 GiB, 75%, 10.5 MiB/s"
        m = RE_TRANSFERRED_BYTES.search(line)
        assert m is not None
        assert m.group(1) == "90.054 GiB"
        assert m.group(3) == "75"

    def test_transferred_files(self):
        line = "Transferred:       42 / 100, 42%"
        m = RE_TRANSFERRED_FILES.search(line)
        assert m is not None
        assert m.group(1) == "42"
        assert m.group(2) == "100"

    def test_elapsed(self):
        line = "Elapsed time:  14h59m30.0s"
        m = RE_ELAPSED.search(line)
        assert m is not None
        assert "14h59m30.0s" in m.group(1)

    def test_errors(self):
        line = "Errors:         3"
        m = RE_ERRORS.search(line)
        assert m is not None
        assert m.group(1) == "3"

    def test_speed(self):
        m = RE_SPEED.search("10.5 MiB/s")
        assert m is not None
        assert m.group(1) == "10.5"
        assert m.group(2) == "MiB"


# ─── validate_rclone_input ───────────────────────────────────────────────────


class TestValidateRcloneInput:
    def test_empty_string_allowed(self):
        assert validate_rclone_input("", "source") is True

    def test_none_allowed(self):
        assert validate_rclone_input(None, "source") is True

    def test_normal_remote(self):
        assert validate_rclone_input("gdrive:", "source") is True

    def test_normal_path(self):
        assert validate_rclone_input("/home/user/docs", "dest") is True

    def test_rejects_double_dash_flag(self):
        assert validate_rclone_input("--config=/etc/passwd", "test") is False

    def test_rejects_single_dash_flag(self):
        assert validate_rclone_input("-v", "test") is False

    def test_rejects_newline(self):
        assert validate_rclone_input("gdrive:\nmalicious", "source") is False

    def test_rejects_carriage_return(self):
        assert validate_rclone_input("gdrive:\rmalicious", "source") is False

    def test_rejects_null_byte(self):
        assert validate_rclone_input("gdrive:\x00malicious", "source") is False

    def test_allows_colon_in_path(self):
        assert validate_rclone_input("remote:path/to/folder", "source") is True

    def test_allows_spaces(self):
        assert validate_rclone_input("remote:My Documents", "source") is True

    # Allowlist tests
    def test_allows_unicode_path(self):
        assert validate_rclone_input("remote:文档/照片", "source") is True

    def test_allows_arabic_path(self):
        assert validate_rclone_input("remote:مستندات", "source") is True

    def test_rejects_backticks(self):
        assert validate_rclone_input("remote:`whoami`", "source") is False

    def test_rejects_dollar_paren(self):
        assert validate_rclone_input("remote:$(cat /etc/passwd)", "source") is False

    def test_rejects_semicolon(self):
        assert validate_rclone_input("remote:path; rm -rf /", "source") is False

    def test_rejects_pipe(self):
        assert validate_rclone_input("remote:path|cat /etc/passwd", "source") is False

    def test_rejects_ampersand(self):
        assert validate_rclone_input("remote:path&&malicious", "source") is False


# ─── validate_exclude_pattern ─────────────────────────────────────────────────


class TestValidateExcludePattern:
    def test_simple_pattern(self):
        assert validate_exclude_pattern("*.tmp") is True

    def test_glob_star_pattern(self):
        assert validate_exclude_pattern("**/.git") is True

    def test_rejects_curly_braces(self):
        assert validate_exclude_pattern("{a,b}") is False

    def test_rejects_square_brackets(self):
        assert validate_exclude_pattern("[abc]") is False

    def test_rejects_closing_curly(self):
        assert validate_exclude_pattern("test}") is False

    def test_rejects_closing_square(self):
        assert validate_exclude_pattern("test]") is False

    def test_rejects_flags(self):
        assert validate_exclude_pattern("--delete") is False

    def test_rejects_newline(self):
        assert validate_exclude_pattern("*.tmp\nmalicious") is False

    def test_empty_allowed(self):
        assert validate_exclude_pattern("") is True

    def test_path_separator(self):
        assert validate_exclude_pattern("dir/file.txt") is True


# ─── _sanitize_rclone_error ──────────────────────────────────────────────────


class TestSanitizeRcloneError:
    def test_empty_string(self):
        assert _sanitize_rclone_error("") == "Connection failed. Please try again."

    def test_none(self):
        assert _sanitize_rclone_error(None) == "Connection failed. Please try again."

    def test_address_in_use(self):
        result = _sanitize_rclone_error("2024/01/01 12:00:00 ERROR : address already in use")
        assert "busy" in result.lower()

    def test_token_error(self):
        result = _sanitize_rclone_error("2024/01/01 12:00:00 ERROR : Failed to get token")
        assert "Authentication failed" in result

    def test_oauth_error(self):
        result = _sanitize_rclone_error("2024/01/01 12:00:00 ERROR : oauth flow failed")
        assert "Authentication failed" in result

    def test_timeout_error(self):
        result = _sanitize_rclone_error("connection timeout reached")
        assert "timed out" in result.lower()

    def test_timed_out_error(self):
        result = _sanitize_rclone_error("request timed out while connecting")
        assert "timed out" in result.lower()

    def test_long_error_truncated(self):
        long_msg = "x" * 200
        result = _sanitize_rclone_error(long_msg)
        assert result == "Connection failed. Please try again."

    def test_short_generic_error(self):
        result = _sanitize_rclone_error("Something went wrong")
        assert result == "Something went wrong"

    def test_multiline_takes_first(self):
        result = _sanitize_rclone_error("first line\nsecond line\nthird line")
        assert result == "first line"

    def test_error_prefix_stripped(self):
        result = _sanitize_rclone_error("2024/01/01 12:00:00 ERROR : some problem here")
        assert result == "some problem here"

    def test_notice_prefix_stripped(self):
        result = _sanitize_rclone_error("2024/01/01 12:00:00 NOTICE : some notice here")
        assert result == "some notice here"

    def test_no_error_keyword_not_stripped(self):
        result = _sanitize_rclone_error("simple error message")
        assert result == "simple error message"


# ─── to_bytes ─────────────────────────────────────────────────────────────────


class TestToBytes:
    def test_gib(self):
        assert to_bytes("90.054 GiB") == pytest.approx(90.054 * 1024**3)

    def test_mib(self):
        assert to_bytes("103.010 MiB") == pytest.approx(103.010 * 1024**2)

    def test_kib(self):
        assert to_bytes("512 KiB") == pytest.approx(512 * 1024)

    def test_tib(self):
        assert to_bytes("2.5 TiB") == pytest.approx(2.5 * 1024**4)

    def test_gb_decimal(self):
        assert to_bytes("1.5 GB") == pytest.approx(1.5 * 1000**3)

    def test_mb_decimal(self):
        assert to_bytes("500 MB") == pytest.approx(500 * 1000**2)

    def test_kb_decimal(self):
        assert to_bytes("100 KB") == pytest.approx(100 * 1000)

    def test_tb_decimal(self):
        assert to_bytes("1 TB") == pytest.approx(1 * 1000**4)

    def test_zero_bytes(self):
        assert to_bytes("0 B") == 0.0

    def test_plain_bytes(self):
        # No recognized unit suffix -> returns raw value
        assert to_bytes("1234 B") == 1234.0

    def test_empty_string(self):
        assert to_bytes("") == 0

    def test_whitespace(self):
        assert to_bytes("   ") == 0

    def test_no_match(self):
        assert to_bytes("invalid") == 0

    def test_leading_whitespace(self):
        assert to_bytes("  90.054 GiB  ") == pytest.approx(90.054 * 1024**3)

    def test_gi_variant(self):
        # "GI" should also match GiB branch
        assert to_bytes("1 Gi") == pytest.approx(1024**3)

    def test_mi_variant(self):
        assert to_bytes("1 Mi") == pytest.approx(1024**2)

    def test_ki_variant(self):
        assert to_bytes("1 Ki") == pytest.approx(1024)

    def test_ti_variant(self):
        assert to_bytes("1 Ti") == pytest.approx(1024**4)


# ─── fmt_bytes ────────────────────────────────────────────────────────────────


class TestFmtBytes:
    def test_tib(self):
        assert fmt_bytes(2 * 1024**4) == "2.00 TiB"

    def test_gib(self):
        assert fmt_bytes(5.5 * 1024**3) == "5.50 GiB"

    def test_mib(self):
        assert fmt_bytes(100 * 1024**2) == "100.00 MiB"

    def test_kib(self):
        assert fmt_bytes(512 * 1024) == "512.00 KiB"

    def test_bytes(self):
        assert fmt_bytes(42) == "42 B"

    def test_zero(self):
        assert fmt_bytes(0) == "0 B"

    def test_just_below_kib(self):
        assert fmt_bytes(1023) == "1023 B"

    def test_exactly_one_kib(self):
        assert fmt_bytes(1024) == "1.00 KiB"

    def test_exactly_one_gib(self):
        assert fmt_bytes(1024**3) == "1.00 GiB"

    def test_exactly_one_tib(self):
        assert fmt_bytes(1024**4) == "1.00 TiB"


# ─── parse_elapsed ────────────────────────────────────────────────────────────


class TestParseElapsed:
    def test_full_hms(self):
        assert parse_elapsed("14h59m30.0s") == pytest.approx(14 * 3600 + 59 * 60 + 30.0)

    def test_minutes_seconds(self):
        assert parse_elapsed("28m0.0s") == pytest.approx(28 * 60)

    def test_seconds_only(self):
        assert parse_elapsed("45.5s") == pytest.approx(45.5)

    def test_hours_only(self):
        assert parse_elapsed("2h") == pytest.approx(7200)

    def test_minutes_only(self):
        assert parse_elapsed("5m") == pytest.approx(300)

    def test_hours_seconds(self):
        assert parse_elapsed("1h30.5s") == pytest.approx(3630.5)

    def test_empty_string(self):
        assert parse_elapsed("") == 0.0

    def test_no_match(self):
        assert parse_elapsed("nothing") == 0.0

    def test_fractional_seconds(self):
        assert parse_elapsed("0.123s") == pytest.approx(0.123)


# ─── fmt_duration ─────────────────────────────────────────────────────────────


class TestFmtDuration:
    def test_zero(self):
        assert fmt_duration(0) == "0s"

    def test_negative(self):
        assert fmt_duration(-5) == "0s"

    def test_seconds_only(self):
        assert fmt_duration(45) == "45s"

    def test_minutes_seconds(self):
        assert fmt_duration(125) == "2m 5s"

    def test_hours_minutes_seconds(self):
        assert fmt_duration(3661) == "1h 1m 1s"

    def test_days(self):
        assert fmt_duration(90061) == "1d 1h 1m 1s"

    def test_exact_hour(self):
        assert fmt_duration(3600) == "1h"

    def test_exact_minute(self):
        assert fmt_duration(60) == "1m"

    def test_exact_day(self):
        assert fmt_duration(86400) == "1d"

    def test_days_and_seconds(self):
        assert fmt_duration(86401) == "1d 1s"

    def test_hours_and_seconds_no_minutes(self):
        assert fmt_duration(3601) == "1h 1s"

    def test_days_hours(self):
        assert fmt_duration(90000) == "1d 1h"

    def test_fractional_seconds_truncated(self):
        assert fmt_duration(1.9) == "1s"

    def test_small_fraction_shows_0s(self):
        # 0.5 sec -> sec=0, parts is empty -> appends "0s"
        assert fmt_duration(0.5) == "0s"


# ─── downsample ───────────────────────────────────────────────────────────────


class TestDownsample:
    def test_short_list_unchanged(self):
        arr = [1, 2, 3]
        assert downsample(arr, target=10) == [1, 2, 3]

    def test_exact_target_unchanged(self):
        arr = list(range(200))
        assert downsample(arr, target=200) == arr

    def test_reduces_length(self):
        arr = list(range(1000))
        result = downsample(arr, target=100)
        assert len(result) <= 102  # target + possible last element

    def test_includes_first_element(self):
        arr = list(range(500))
        result = downsample(arr, target=50)
        assert result[0] == 0

    def test_includes_last_element(self):
        arr = list(range(500))
        result = downsample(arr, target=50)
        assert result[-1] == 499

    def test_empty_list(self):
        assert downsample([], target=10) == []

    def test_single_element(self):
        assert downsample([42], target=10) == [42]

    def test_target_one(self):
        arr = list(range(100))
        result = downsample(arr, target=1)
        assert result[0] == 0
        assert result[-1] == 99
        assert len(result) == 2  # first sample + appended last

    def test_default_target(self):
        arr = list(range(500))
        result = downsample(arr)
        assert len(result) <= CHART_DOWNSAMPLE_TARGET + 1

    def test_last_element_not_duplicated(self):
        # When the last sampled element is already the last element
        arr = list(range(200))
        # target=200 returns arr unchanged
        result = downsample(arr, target=200)
        assert result[-1] == 199
        # no duplicate
        assert result.count(199) == 1


# ─── get_remote_label ─────────────────────────────────────────────────────────


class TestGetRemoteLabel:
    def test_onedrive(self):
        assert get_remote_label("onedrive:") == "OneDrive"

    def test_gdrive(self):
        assert get_remote_label("gdrive:") == "Google Drive"

    def test_gdrive_subfolder(self):
        assert get_remote_label("gdrive:backup") == "Google Drive/backup"

    def test_dropbox(self):
        assert get_remote_label("dropbox:") == "Dropbox"

    def test_mega(self):
        assert get_remote_label("mega:") == "MEGA"

    def test_s3(self):
        assert get_remote_label("s3:bucket") == "Amazon S3/bucket"

    def test_b2(self):
        assert get_remote_label("b2:bucket") == "Backblaze B2/bucket"

    def test_box(self):
        assert get_remote_label("box:") == "Box"

    def test_ftp(self):
        assert get_remote_label("ftp:server") == "FTP/server"

    def test_sftp(self):
        result = get_remote_label("sftp:server")
        assert result == "SFTP/server"

    def test_protondrive(self):
        assert get_remote_label("protondrive:") == "Proton Drive"

    def test_local_path_slash(self):
        assert get_remote_label("/home/user/docs") == "Local"

    def test_local_path_dot(self):
        assert get_remote_label("./relative/path") == "Local"

    def test_local_no_colon(self):
        assert get_remote_label("some_folder") == "Local"

    def test_unknown_remote(self):
        assert get_remote_label("myremote:stuff") == "myremote"

    def test_case_insensitive(self):
        assert get_remote_label("OneDrive:") == "OneDrive"

    def test_drive_keyword(self):
        assert get_remote_label("drive:") == "Google Drive"

    def test_local_keyword(self):
        assert get_remote_label("local:path") == "Local/path"

    def test_gdrive_deep_subfolder(self):
        assert get_remote_label("gdrive:a/b/c") == "Google Drive/a/b/c"

    def test_sftp_exact_match(self):
        """SFTP should not be misidentified as FTP."""
        assert get_remote_label("sftp:server") == "SFTP/server"

    def test_sftp_no_subfolder(self):
        assert get_remote_label("sftp:") == "SFTP"

    def test_bare_colon(self):
        """A bare colon with no remote name should return 'Local'."""
        assert get_remote_label(":") == "Local"


# ─── downsample edge cases ──────────────────────────────────────────────────


class TestDownsampleEdgeCases:
    def test_target_zero_returns_original(self):
        """target=0 should not crash with ZeroDivisionError."""
        arr = [1, 2, 3]
        assert downsample(arr, target=0) == arr

    def test_target_negative_returns_original(self):
        arr = [1, 2, 3]
        assert downsample(arr, target=-1) == arr


# ─── fmt_bytes edge cases ───────────────────────────────────────────────────


class TestFmtBytesEdgeCases:
    def test_negative_bytes(self):
        """Negative byte values should not crash."""
        result = fmt_bytes(-1)
        assert "B" in result


# ─── Unicode Log Parsing ────────────────────────────────────────────────────


class TestUnicodeLogParsing:
    def test_unicode_filename_copied(self):
        """RE_COPIED matches Japanese Unicode filenames."""
        line = "INFO  : \u4f8b\u3048\u3070.txt: Copied (new)"
        m = RE_COPIED.search(line)
        assert m is not None
        assert m.group(1) == "\u4f8b\u3048\u3070.txt"

    def test_arabic_filename_copied(self):
        """RE_COPIED matches Arabic Unicode filenames."""
        line = "INFO  : \u0645\u0633\u062a\u0646\u062f.pdf: Copied (new)"
        m = RE_COPIED.search(line)
        assert m is not None
        assert m.group(1) == "\u0645\u0633\u062a\u0646\u062f.pdf"

    def test_unicode_path_with_timestamp(self):
        """RE_COPIED_WITH_TS matches Unicode filenames with timestamps."""
        line = "2024/01/15 13:01:22 INFO  : \u4f8b\u3048\u3070/\u30c6\u30b9\u30c8.txt: Copied (new)"
        m = RE_COPIED_WITH_TS.search(line)
        assert m is not None
        assert m.group(1) == "2024/01/15 13:01:22"
        assert "\u4f8b\u3048\u3070" in m.group(2)

    def test_error_with_special_chars(self):
        """RE_ERROR_MSG matches error lines with special characters."""
        line = "13:01:22 ERROR : \u00e9\u00e8\u00ea file\u2019s: connection failed"
        m = RE_ERROR_MSG.search(line)
        assert m is not None
        assert "\u00e9\u00e8\u00ea" in m.group(1)

    def test_mixed_ascii_unicode_filename(self):
        """RE_COPIED handles mixed ASCII and Unicode characters."""
        line = "INFO  : photos/vacation_\u65c5\u884c_2024.jpg: Copied (new)"
        m = RE_COPIED.search(line)
        assert m is not None
        assert "vacation_\u65c5\u884c_2024.jpg" in m.group(1)

    def test_emoji_in_filename(self):
        """RE_COPIED matches filenames with emoji characters."""
        line = "INFO  : \U0001f4c1documents/\U0001f3b5music.mp3: Copied (new)"
        m = RE_COPIED.search(line)
        assert m is not None
        assert "\U0001f4c1" in m.group(1)

    def test_active_transfer_unicode(self):
        """RE_ACTIVE matches active transfer with Unicode filename."""
        line = "*  \u6587\u66f8.zip:  45% /500MiB, 30MiB/s, 10s"
        m = RE_ACTIVE.search(line)
        assert m is not None
        assert m.group(1) == "\u6587\u66f8.zip"

    def test_transferred_bytes_with_unicode_context(self):
        """RE_TRANSFERRED_BYTES works in presence of surrounding Unicode."""
        line = "Transferred:   90.054 GiB / 120.000 GiB, 75%, 10.5 MiB/s"
        m = RE_TRANSFERRED_BYTES.search(line)
        assert m is not None
        assert m.group(1) == "90.054 GiB"
