"""Display helpers: colors, badges, relative times."""

from datetime import UTC, datetime


def get_margin_color(delta_pct: float | None) -> str:
    if delta_pct is None:
        return "gray"
    elif delta_pct <= -20:
        return "#00ff00"
    elif delta_pct <= -10:
        return "#90EE90"
    elif delta_pct <= 0:
        return "#D3D3D3"
    else:
        return "#FFB6C6"


def format_liquidity_stars(score: float | None) -> str:
    if score is None:
        return "N/A"
    stars = int(score * 5)
    return "stars" * stars if stars > 0 else "---"


def format_trend_indicator(score: float | None) -> str:
    if score is None:
        return "---"
    elif score > 0.5:
        return "Hot"
    elif score > 0:
        return "Stable"
    else:
        return "Cooling"


def format_discount(delta_pct: float | None) -> str:
    """Convert negative delta_vs_pmn_pct to a human-friendly discount string."""
    if delta_pct is None:
        return "N/A"
    discount = -delta_pct
    if discount > 0:
        return f"{discount:.0f}% below PMN"
    elif discount < 0:
        return f"{-discount:.0f}% above PMN"
    return "At PMN"


def confidence_badge(confidence: float | None) -> str:
    if confidence is None:
        return "--- N/A"
    if confidence >= 0.7:
        return f"High ({confidence:.0%})"
    elif confidence >= 0.4:
        return f"Medium ({confidence:.0%})"
    else:
        return f"Low ({confidence:.0%})"


def status_dot(status: str) -> str:
    colors = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    return colors.get(status, "⚪")


def relative_time(iso_str: str | None) -> str:
    if not iso_str:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = now - dt
        seconds = delta.total_seconds()
        if seconds < 60:
            return "Just now"
        elif seconds < 3600:
            m = int(seconds // 60)
            return f"{m}m ago"
        elif seconds < 86400:
            h = int(seconds // 3600)
            return f"{h}h ago"
        else:
            d = int(seconds // 86400)
            return f"{d}d ago"
    except (ValueError, TypeError):
        return "Unknown"
