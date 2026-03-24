"""Shared reusable UI components: paginator, KPI row, empty state, confirm action."""

from collections.abc import Callable

import streamlit as st


def paginator(
    session_key: str,
    total: int,
    page_size: int,
    *,
    show_total: bool = True,
) -> int:
    """Render a pagination row and return the current offset.

    Args:
        session_key: Session state key for storing page number (e.g. "discovery_page").
        total: Total number of items.
        page_size: Items per page.
        show_total: Whether to show "Showing X-Y of Z" caption.

    Returns:
        Current offset (0-based).
    """
    if session_key not in st.session_state:
        st.session_state[session_key] = 0

    current_page: int = st.session_state[session_key]
    total_pages = max(1, (total + page_size - 1) // page_size)

    # Clamp page to valid range
    if current_page >= total_pages:
        current_page = total_pages - 1
        st.session_state[session_key] = current_page

    offset = current_page * page_size

    col_prev, col_info, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("Previous", disabled=current_page == 0, key=f"{session_key}_prev"):
            st.session_state[session_key] = current_page - 1
            st.rerun()
    with col_info:
        if show_total:
            start = offset + 1
            end = min(offset + page_size, total)
            st.caption(f"Page {current_page + 1} of {total_pages} ({start}-{end} of {total})")
        else:
            st.caption(f"Page {current_page + 1} of {total_pages}")
    with col_next:
        if st.button("Next", disabled=current_page >= total_pages - 1, key=f"{session_key}_next"):
            st.session_state[session_key] = current_page + 1
            st.rerun()

    return offset


def kpi_row(metrics: list[dict]) -> None:
    """Render a row of KPI metric cards.

    Args:
        metrics: List of dicts with keys: label, value, delta (optional),
                 delta_color (optional, "normal"/"inverse"/"off").
    """
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics, strict=True):
        with col:
            st.metric(
                label=m["label"],
                value=m["value"],
                delta=m.get("delta"),
                delta_color=m.get("delta_color", "normal"),
            )


def empty_state(
    title: str,
    message: str,
    *,
    action_label: str | None = None,
    action_page: str | None = None,
) -> None:
    """Render a centered empty-state message with optional navigation.

    Args:
        title: Bold heading text.
        message: Descriptive body text.
        action_label: Optional button label.
        action_page: Page path to navigate to if button is clicked.
    """
    st.markdown(f"#### {title}")
    st.markdown(message)
    if action_label and action_page:
        st.page_link(action_page, label=action_label, icon=":material/arrow_forward:")


def confirm_action(
    key: str,
    label: str,
    on_confirm: Callable[[], None],
    *,
    confirm_label: str = "Confirm",
    danger: bool = True,
) -> None:
    """Two-click destructive action pattern.

    First click shows confirmation; second click executes.

    Args:
        key: Unique key for this action.
        label: Initial button label.
        on_confirm: Callback to execute on confirmation.
        confirm_label: Text for the confirmation button.
        danger: Whether to style as destructive.
    """
    confirm_key = f"_confirm_{key}"

    if st.session_state.get(confirm_key):
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button(
                confirm_label,
                key=f"{key}_yes",
                type="primary" if not danger else "secondary",
                use_container_width=True,
            ):
                on_confirm()
                st.session_state[confirm_key] = False
                st.rerun()
        with col_no:
            if st.button("Cancel", key=f"{key}_cancel", use_container_width=True):
                st.session_state[confirm_key] = False
                st.rerun()
    else:
        if st.button(label, key=f"{key}_trigger", type="secondary"):
            st.session_state[confirm_key] = True
            st.rerun()
