"""Display helpers: colors, badges, relative times."""

import math
from datetime import UTC, datetime

from ui.lib.theme import COLORS


def _is_missing(v: float | None) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def get_margin_color(delta_pct: float | None) -> str:
    if _is_missing(delta_pct):
        return COLORS["muted"]
    elif delta_pct <= -20:
        return COLORS["margin_great"]
    elif delta_pct <= -10:
        return COLORS["margin_good"]
    elif delta_pct <= 0:
        return COLORS["margin_neutral"]
    else:
        return COLORS["margin_bad"]


def format_liquidity_score(score: float | None) -> str:
    """Format liquidity as a fraction out of 5."""
    if _is_missing(score):
        return "N/A"
    level = int(score * 5)
    return f"{level}/5"


def format_trend_indicator(score: float | None) -> str:
    """Return an HTML badge for trend direction."""
    if _is_missing(score):
        return '<span class="badge badge-gray">--- N/A</span>'
    elif score > 0.5:
        return f'<span class="badge badge-green">&#9650; Hot ({score:.2f})</span>'
    elif score > 0:
        return f'<span class="badge badge-blue">&#9654; Stable ({score:.2f})</span>'
    else:
        return f'<span class="badge badge-red">&#9660; Cooling ({score:.2f})</span>'


def format_discount(delta_pct: float | None) -> str:
    """Convert negative delta_vs_pmn_pct to a human-friendly discount string."""
    if _is_missing(delta_pct):
        return "N/A"
    discount = -delta_pct
    if discount > 0:
        return f"{discount:.0f}% below PMN"
    elif discount < 0:
        return f"{-discount:.0f}% above PMN"
    return "At PMN"


def confidence_badge(confidence: float | None) -> str:
    """Return an HTML badge for PMN confidence level."""
    if _is_missing(confidence):
        return '<span class="badge badge-gray">N/A</span>'
    pct = f"{confidence:.0%}"
    if confidence >= 0.7:
        return f'<span class="badge badge-green">High ({pct})</span>'
    elif confidence >= 0.4:
        return f'<span class="badge badge-yellow">Medium ({pct})</span>'
    else:
        return f'<span class="badge badge-red">Low ({pct})</span>'


def format_spread(spread_eur: float | None) -> str:
    """Format arbitrage spread as colored string."""
    if spread_eur is None:
        return "---"
    if spread_eur >= 0:
        return f"+€{spread_eur:.2f}"
    return f"-€{abs(spread_eur):.2f}"


def format_roi(roi_pct: float | None) -> str:
    """Format ROI percentage."""
    if roi_pct is None:
        return "---"
    return f"{roi_pct:.1f}%"


def format_score_badge(value: float | None, max_val: float = 100.0) -> str:
    """Format a 0-max_val score as a display string."""
    if value is None:
        return "---"
    return f"{value:.0f}/{max_val:.0f}"


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
