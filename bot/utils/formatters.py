from datetime import datetime
from typing import Optional


def format_datetime(dt: Optional[datetime], format_str: str = "%d.%m.%Y %H:%M") -> str:
    """Format datetime to string"""
    if not dt:
        return "N/A"
    return dt.strftime(format_str)


def format_number(num: int) -> str:
    """Format number with thousands separator"""
    return f"{num:,}".replace(",", " ")


def escape_html(text: str) -> str:
    """Escape HTML special characters"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
