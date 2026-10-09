"""Per-recipient validation.

Validation is per item (not all-or-nothing) so that one bad row never blocks the
rest of a bulk request. Each problem is returned as a human-readable string.
"""
from typing import Any

from email_validator import EmailNotValidError, validate_email

MAX_NAME_LENGTH = 100


def validate_recipient(
    raw: Any, *, unicode_font: bool = False
) -> tuple[dict | None, str | None]:
    """Return (clean_recipient, None) or (None, error_message)."""
    if not isinstance(raw, dict):
        return None, "recipient must be an object with 'name' and 'email'"

    errors: list[str] = []

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name is required")
        name = None
    else:
        name = " ".join(name.split())  # trim + collapse whitespace
        if len(name) > MAX_NAME_LENGTH:
            errors.append(f"name must be at most {MAX_NAME_LENGTH} characters")
        elif any(ord(c) < 32 for c in name):
            errors.append("name contains control characters")
        elif not unicode_font:
            try:
                name.encode("cp1252")
            except UnicodeEncodeError:
                errors.append(
                    "name contains characters the certificate font cannot render "
                    "(set CERT_FONT_PATH to a Unicode TTF font to support them)"
                )

    email = raw.get("email")
    if not isinstance(email, str) or not email.strip():
        errors.append("email is required")
        email = None
    else:
        try:
            email = validate_email(email.strip(), check_deliverability=False).normalized
        except EmailNotValidError as exc:
            errors.append(f"invalid email: {exc}")
            email = None

    if errors:
        return None, "; ".join(errors)
    return {"name": name, "email": email}, None
