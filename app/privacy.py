"""Remove government ID numbers from text before anything else sees it (model, notes, memory service, search)."""
import re

PATTERNS = [
    re.compile(r"(?<![\dA-Za-z])[A-Za-z]{2}\s?\d{8}\s?[A-Za-z]{2}(?![\dA-Za-z])"),  # residence card: AB12345678CD
    re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)"),                           # My Number: 12 digits
    re.compile(r"(?<![\dA-Za-z])[A-Za-z]{1,2}\s?\d{7,8}(?![\dA-Za-z])"),             # passport: TR1234567
]
MARK = "[ID number removed]"


def redact(text: str):
    """Return (clean text, number of ID-like numbers removed)."""
    total = 0
    for pat in PATTERNS:
        text, n = pat.subn(MARK, text or "")
        total += n
    return text, total
