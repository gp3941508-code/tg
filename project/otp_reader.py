"""
OTP interception / reading login codes from Telegram (e.g., 777000) is intentionally NOT implemented.

This module only provides a safe OTP extraction helper for text you already legitimately have.
"""

from __future__ import annotations

import re


OTP_REGEX = re.compile(r"\b(\d{4,8})\b")


def extract_otp(text: str) -> str | None:
    m = OTP_REGEX.search(text or "")
    return m.group(1) if m else None

