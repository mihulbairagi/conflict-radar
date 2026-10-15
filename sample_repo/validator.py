"""validator.py - Request payload validation and sanitization logic."""

from utils import normalize


def validate_input(payload: dict) -> bool:
    """Validates required payload fields and checks content normalization."""
    if not isinstance(payload, dict):
        return False
    name = payload.get("username", "")
    normalized_name = normalize(name)
    return len(normalized_name) > 3


def sanitize_header(header_value: str) -> str:
    """Removes unsafe characters from HTTP header inputs."""
    return header_value.replace("\r", "").replace("\n", "")
