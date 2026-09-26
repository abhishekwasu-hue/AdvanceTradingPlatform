"""Password policy (Phase C2). Deliberately simple and explainable: length, a little variety,
not on the short list of passwords everyone tries first, and not built from the user's own
email. Complexity theatre (mandatory symbols, forced rotation) is avoided on purpose - length
and a blocklist are what actually stop credential stuffing and brute force."""
import re
from typing import Optional

MIN_LENGTH = 10
MAX_LENGTH = 128

# The passwords that appear in essentially every breach corpus. Compared case-insensitively.
COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "p@ssw0rd", "qwerty123", "qwertyuiop", "1234567890",
    "123456789", "12345678", "iloveyou", "admin123", "administrator", "welcome1", "welcome123", "letmein",
    "trustno1", "sunshine", "princess", "football", "baseball", "monkey123", "dragon123", "master123",
    "abc123456", "abcd1234", "1q2w3e4r", "1qaz2wsx", "qazwsx123", "zaq12wsx", "india123", "india@123",
    "mumbai123", "delhi123", "trading123", "trader123", "nifty123", "nifty50", "banknifty", "zerodha123",
    "upstox123", "changeme", "changeme123", "temp1234", "test1234", "demo1234", "secret123", "pass1234",
    "password!", "password@1", "password@123", "welcome@123", "admin@123", "user@123", "login123",
}

_CLASSES = (re.compile(r"[a-z]"), re.compile(r"[A-Z]"), re.compile(r"[0-9]"), re.compile(r"[^A-Za-z0-9]"))


def password_problem(password: str, email: Optional[str] = None) -> Optional[str]:
    """Returns a human-readable reason the password is unacceptable, or None when it is fine."""
    if len(password) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters"
    if len(password) > MAX_LENGTH:
        return f"Password must be at most {MAX_LENGTH} characters"
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or lowered.rstrip("0123456789!@#$") in COMMON_PASSWORDS:
        return "That password is on the list of most common passwords - choose something less guessable"
    if len(set(password)) < 4:
        return "Password needs more variety than a repeated character"
    if sum(1 for cls in _CLASSES if cls.search(password)) < 2:
        return "Password must mix at least two of: lowercase, uppercase, digits, symbols"
    if email:
        local = email.split("@")[0].lower()
        if len(local) >= 4 and local in lowered:
            return "Password must not contain your email address"
    return None


def validate_password(password: str, email: Optional[str] = None) -> None:
    problem = password_problem(password, email)
    if problem:
        raise ValueError(problem)
