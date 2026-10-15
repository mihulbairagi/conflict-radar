"""utils.py - Low-level string manipulation and response formatting utilities."""

def normalize(text: str) -> str:
    """Standardizes string casing and trims surrounding whitespace."""
    if not text:
        return ""
    return text.strip().lower()


def format_response(status_code: int, message: str) -> dict:
    """Constructs a standard API response envelope."""
    return {
        "status": status_code,
        "message": message,
        "success": 200 <= status_code < 300
    }
