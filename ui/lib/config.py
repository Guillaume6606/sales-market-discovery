"""UI configuration: API URL resolution and constants."""

import os

import httpx
import streamlit as st

APP_VERSION = "0.2.0"

SUPPORTED_PROVIDERS: list[str] = ["ebay", "leboncoin", "vinted"]
DEFAULT_TIMEOUT: float = 15.0


def get_api_url() -> str:
    """Return the API base URL, probing Docker then localhost on first call."""
    if "_api_url" in st.session_state:
        return st.session_state["_api_url"]

    env_url = os.environ.get("API_URL", "http://backend:8000")

    # Try the configured/default URL first
    try:
        httpx.get(f"{env_url}/health", timeout=2)
        st.session_state["_api_url"] = env_url
        return env_url
    except Exception:  # noqa: S110
        pass

    # Fall back to localhost for local development
    fallback = "http://localhost:8000"
    if fallback != env_url:
        try:
            httpx.get(f"{fallback}/health", timeout=2)
            st.session_state["_api_url"] = fallback
            return fallback
        except Exception:  # noqa: S110
            pass

    # Nothing reachable - use env_url so errors are explicit
    st.session_state["_api_url"] = env_url
    return env_url
