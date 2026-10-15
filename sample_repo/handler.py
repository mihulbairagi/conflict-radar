"""handler.py - Inbound API request handling, logging, and routing."""

from validator import validate_input
from utils import normalize


def parse_request(raw_body: dict) -> dict:
    """Parses incoming request payload after running schema validation."""
    is_valid = validate_input(raw_body)
    if not is_valid:
        raise ValueError("Invalid request format")
    return {"action": "process", "body": raw_body}


def log_request(req_id: str, raw_text: str) -> str:
    """Logs incoming request info with normalized text content."""
    clean_text = normalize(raw_text)
    return f"[{req_id}] {clean_text}"


def dispatch_action(action: str, payload: dict) -> bool:
    """Dispatches a validated action to the corresponding domain handler."""
    return action in ("process", "sync", "notify")
