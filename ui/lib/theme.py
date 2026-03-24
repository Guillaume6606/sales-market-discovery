"""Design tokens, global CSS injection, and shared Plotly layout."""

import streamlit as st

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
COLORS = {
    # Brand
    "primary": "#2563EB",
    "primary_light": "#3B82F6",
    "cta": "#F97316",
    # Semantic
    "success": "#22C55E",
    "success_muted": "#166534",
    "danger": "#EF4444",
    "danger_muted": "#991B1B",
    "warning": "#F59E0B",
    "warning_muted": "#92400E",
    # Surfaces
    "bg": "#0A0A0F",
    "surface": "#111118",
    "surface_hover": "#1A1A24",
    "border": "#1E293B",
    # Text
    "text": "#E2E8F0",
    "text_secondary": "#94A3B8",
    "muted": "#64748B",
    # Margin gradient (best deal → worst)
    "margin_great": "#22C55E",
    "margin_good": "#4ADE80",
    "margin_neutral": "#64748B",
    "margin_bad": "#F87171",
}

# ---------------------------------------------------------------------------
# Global CSS injection
# ---------------------------------------------------------------------------
_GLOBAL_CSS = """
<style>
/* Metric cards */
[data-testid="stMetric"] {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 12px 16px;
}}
[data-testid="stMetricValue"] {{
    font-size: 1.4rem;
    font-weight: 600;
}}
[data-testid="stMetricDelta"] > div {{
    font-size: 0.85rem;
}}

/* Dataframe hover */
[data-testid="stDataFrame"] tbody tr:hover {{
    background: {surface_hover};
}}

/* Scrollbar theming */
::-webkit-scrollbar {{
    width: 6px;
    height: 6px;
}}
::-webkit-scrollbar-track {{
    background: {bg};
}}
::-webkit-scrollbar-thumb {{
    background: {border};
    border-radius: 3px;
}}
::-webkit-scrollbar-thumb:hover {{
    background: {muted};
}}

/* Divider subtle */
[data-testid="stHorizontalBlock"] hr {{
    border-color: {border};
}}

/* Tab styling */
button[data-baseweb="tab"] {{
    font-weight: 500;
}}

/* Badge base */
.badge {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.82rem;
    font-weight: 500;
    line-height: 1.4;
}}
.badge-green {{
    background: {success_muted};
    color: {success};
}}
.badge-yellow {{
    background: {warning_muted};
    color: {warning};
}}
.badge-red {{
    background: {danger_muted};
    color: {danger};
}}
.badge-blue {{
    background: #1E3A5F;
    color: {primary_light};
}}
.badge-gray {{
    background: #1E293B;
    color: {muted};
}}

/* Status dot */
.dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
}}
.dot-green {{ background: {success}; }}
.dot-yellow {{ background: {warning}; }}
.dot-red {{ background: {danger}; }}
.dot-gray {{ background: {muted}; }}

/* KPI strip spacing */
.kpi-strip [data-testid="stMetric"] {{
    margin-bottom: 0;
}}
</style>
""".format(**COLORS)


def inject_global_css() -> None:
    """Inject global CSS once per page render."""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Status badge (replaces emoji status_dot)
# ---------------------------------------------------------------------------
def status_badge(status: str, label: str | None = None) -> str:
    """Return an HTML status badge with colored dot + optional label.

    Args:
        status: One of 'green', 'yellow', 'red', 'gray'.
        label: Text label. Defaults to status-derived label.
    """
    label_map = {
        "green": "Healthy",
        "yellow": "Degraded",
        "red": "Down",
        "gray": "Unknown",
    }
    display_label = label or label_map.get(status, status.title())
    dot_class = f"dot-{status}" if status in ("green", "yellow", "red") else "dot-gray"
    badge_class = f"badge-{status}" if status in ("green", "yellow", "red") else "badge-gray"
    return (
        f'<span class="badge {badge_class}">'
        f'<span class="dot {dot_class}"></span>'
        f"{display_label}</span>"
    )


# ---------------------------------------------------------------------------
# Shared Plotly layout for dark theme
# ---------------------------------------------------------------------------
PLOTLY_LAYOUT: dict = {
    "template": "plotly_dark",
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"family": "monospace", "color": COLORS["text"], "size": 12},
    "xaxis": {
        "gridcolor": COLORS["border"],
        "zerolinecolor": COLORS["border"],
    },
    "yaxis": {
        "gridcolor": COLORS["border"],
        "zerolinecolor": COLORS["border"],
    },
    "margin": {"l": 40, "r": 20, "t": 30, "b": 40},
    "legend": {
        "bgcolor": "rgba(0,0,0,0)",
        "font": {"color": COLORS["text_secondary"]},
    },
    "hoverlabel": {
        "bgcolor": COLORS["surface"],
        "bordercolor": COLORS["border"],
        "font": {"color": COLORS["text"]},
    },
}
