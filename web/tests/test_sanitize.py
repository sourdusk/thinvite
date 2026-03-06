"""Tests for sanitize.py — every validator, every boundary case."""
import pytest
import sanitize


# ---------------------------------------------------------------------------
# is_valid_email
# ---------------------------------------------------------------------------
class TestEmail:
    def test_simple_email(self):
        assert sanitize.is_valid_email("user@example.com") is True

    def test_subdomain_email(self):
        assert sanitize.is_valid_email("user@mail.example.co.uk") is True

    def test_plus_tag(self):
        assert sanitize.is_valid_email("user+tag@example.com") is True

    def test_dot_in_local_part(self):
        assert sanitize.is_valid_email("first.last@example.com") is True

    def test_underscore_in_local_part(self):
        assert sanitize.is_valid_email("first_last@example.com") is True

    def test_exactly_254_chars(self):
        # "@a.com" = 6 chars; local = 248 chars → total = 254
        local = "a" * 248
        assert sanitize.is_valid_email(f"{local}@a.com") is True

    def test_255_chars_rejected(self):
        # "@a.com" = 6 chars; local = 249 chars → total = 255
        local = "a" * 249
        assert sanitize.is_valid_email(f"{local}@a.com") is False

    def test_empty_rejected(self):
        assert sanitize.is_valid_email("") is False

    def test_none_rejected(self):
        assert sanitize.is_valid_email(None) is False

    def test_missing_at_rejected(self):
        assert sanitize.is_valid_email("userexample.com") is False

    def test_no_local_part_rejected(self):
        assert sanitize.is_valid_email("@example.com") is False

    def test_missing_domain_rejected(self):
        assert sanitize.is_valid_email("user@") is False

    def test_single_char_tld_rejected(self):
        assert sanitize.is_valid_email("user@example.c") is False

    def test_missing_tld_rejected(self):
        assert sanitize.is_valid_email("user@example") is False

    def test_script_tag_rejected(self):
        assert sanitize.is_valid_email("<script>@example.com") is False

    def test_sql_injection_rejected(self):
        assert sanitize.is_valid_email("'; DROP TABLE users; --@x.com") is False

    def test_whitespace_rejected(self):
        assert sanitize.is_valid_email("user @example.com") is False


# ---------------------------------------------------------------------------
# is_valid_twitch_username
# ---------------------------------------------------------------------------
class TestTwitchUsername:
    def test_simple_alphanumeric(self):
        assert sanitize.is_valid_twitch_username("user123") is True

    def test_underscore_allowed(self):
        assert sanitize.is_valid_twitch_username("cool_streamer") is True

    def test_mixed_case(self):
        assert sanitize.is_valid_twitch_username("CoolStreamer") is True

    def test_single_char(self):
        assert sanitize.is_valid_twitch_username("x") is True

    def test_exactly_25_chars(self):
        assert sanitize.is_valid_twitch_username("a" * 25) is True

    def test_26_chars_rejected(self):
        assert sanitize.is_valid_twitch_username("a" * 26) is False

    def test_empty_string_rejected(self):
        assert sanitize.is_valid_twitch_username("") is False

    def test_none_rejected(self):
        assert sanitize.is_valid_twitch_username(None) is False

    def test_space_rejected(self):
        assert sanitize.is_valid_twitch_username("user name") is False

    def test_script_tag_rejected(self):
        assert sanitize.is_valid_twitch_username("<script>alert(1)</script>") is False

    def test_sql_injection_rejected(self):
        assert sanitize.is_valid_twitch_username("'; DROP TABLE users; --") is False

    def test_at_sign_rejected(self):
        assert sanitize.is_valid_twitch_username("@user") is False

    def test_dash_rejected(self):
        assert sanitize.is_valid_twitch_username("user-name") is False

    def test_newline_rejected(self):
        assert sanitize.is_valid_twitch_username("user\nname") is False


# ---------------------------------------------------------------------------
# is_valid_uuid
# ---------------------------------------------------------------------------
class TestUUID:
    VALID = "550e8400-e29b-41d4-a716-446655440000"

    def test_canonical_uuid(self):
        assert sanitize.is_valid_uuid(self.VALID) is True

    def test_uppercase_uuid(self):
        assert sanitize.is_valid_uuid(self.VALID.upper()) is True

    def test_no_hyphens_rejected(self):
        assert sanitize.is_valid_uuid("550e8400e29b41d4a716446655440000") is False

    def test_too_short_rejected(self):
        assert sanitize.is_valid_uuid("550e8400-e29b-41d4") is False

    def test_garbage_rejected(self):
        assert sanitize.is_valid_uuid("not-a-uuid") is False

    def test_sql_injection_rejected(self):
        assert sanitize.is_valid_uuid("'; DROP TABLE users; --") is False

    def test_empty_rejected(self):
        assert sanitize.is_valid_uuid("") is False

    def test_none_rejected(self):
        assert sanitize.is_valid_uuid(None) is False

    def test_extra_segment_rejected(self):
        assert sanitize.is_valid_uuid(self.VALID + "-extra") is False


# ---------------------------------------------------------------------------
# is_valid_snowflake
# ---------------------------------------------------------------------------
class TestSnowflake:
    def test_17_digits(self):
        assert sanitize.is_valid_snowflake("12345678901234567") is True

    def test_18_digits(self):
        assert sanitize.is_valid_snowflake("123456789012345678") is True

    def test_20_digits(self):
        assert sanitize.is_valid_snowflake("12345678901234567890") is True

    def test_integer_input(self):
        assert sanitize.is_valid_snowflake(123456789012345678) is True

    def test_16_digits_rejected(self):
        assert sanitize.is_valid_snowflake("1234567890123456") is False

    def test_21_digits_rejected(self):
        assert sanitize.is_valid_snowflake("123456789012345678901") is False

    def test_letters_rejected(self):
        assert sanitize.is_valid_snowflake("abc123def456ghi78") is False

    def test_empty_rejected(self):
        assert sanitize.is_valid_snowflake("") is False

    def test_none_rejected(self):
        assert sanitize.is_valid_snowflake(None) is False

    def test_sql_injection_rejected(self):
        assert sanitize.is_valid_snowflake("1' OR '1'='1") is False


# ---------------------------------------------------------------------------
# is_positive_int
# ---------------------------------------------------------------------------
class TestPositiveInt:
    def test_integer_1(self):
        assert sanitize.is_positive_int(1) is True

    def test_large_int(self):
        assert sanitize.is_positive_int(99999) is True

    def test_string_digit(self):
        assert sanitize.is_positive_int("42") is True

    def test_zero_rejected(self):
        assert sanitize.is_positive_int(0) is False

    def test_negative_rejected(self):
        assert sanitize.is_positive_int(-1) is False

    def test_none_rejected(self):
        assert sanitize.is_positive_int(None) is False

    def test_float_string_rejected(self):
        assert sanitize.is_positive_int("3.14") is False

    def test_empty_string_rejected(self):
        assert sanitize.is_positive_int("") is False

    def test_alpha_rejected(self):
        assert sanitize.is_positive_int("abc") is False

    def test_sql_injection_rejected(self):
        assert sanitize.is_positive_int("1; DROP TABLE redemptions") is False
