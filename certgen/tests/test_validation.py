import pytest

from app.validation import validate_recipient


def test_valid_recipient_is_normalised():
    clean, err = validate_recipient({"name": "  Asha   Rao ", "email": " Asha@Example.COM "})
    assert err is None
    assert clean == {"name": "Asha Rao", "email": "Asha@example.com"}


@pytest.mark.parametrize("raw,fragment", [
    (None, "must be an object"),
    ([], "must be an object"),
    ({}, "name is required"),
    ({"name": 5, "email": "a@b.co"}, "name is required"),
    ({"name": "x" * 101, "email": "a@b.co"}, "at most 100"),
    ({"name": "a\x00b", "email": "a@b.co"}, "control characters"),
    ({"name": "Ok", "email": "bad"}, "invalid email"),
    ({"name": "Ok", "email": None}, "email is required"),
])
def test_invalid_recipients(raw, fragment):
    clean, err = validate_recipient(raw)
    assert clean is None and fragment in err


def test_multiple_problems_reported_together():
    _, err = validate_recipient({"name": "", "email": "bad"})
    assert "name is required" in err and "invalid email" in err


def test_unicode_names_allowed_only_with_unicode_font():
    assert validate_recipient({"name": "आशा", "email": "a@example.com"})[1] is not None
    assert validate_recipient({"name": "आशा", "email": "a@example.com"}, unicode_font=True)[1] is None
