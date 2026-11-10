"""Worker topology: one primary worker plus an optional remote worker for some providers.

Safe to import from the backend: no connector or scraping imports.
"""

from typing import Literal

from arq.constants import default_queue_name

from ingestion.constants import SUPPORTED_PROVIDERS
from libs.common.settings import settings

WorkerRole = Literal["primary", "remote"]

QUEUES: dict[WorkerRole, str] = {"primary": default_queue_name, "remote": "arq:queue:remote"}
HEARTBEAT_KEYS: dict[WorkerRole, str] = {
    "primary": "worker:heartbeat",
    "remote": "worker:heartbeat:remote",
}


def remote_providers() -> list[str]:
    providers = [p.strip() for p in settings.remote_providers.split(",") if p.strip()]
    unknown = sorted(set(providers) - set(SUPPORTED_PROVIDERS))
    if unknown:
        raise ValueError(f"Unsupported REMOTE_PROVIDERS: {', '.join(unknown)}")
    return providers


def provider_role(provider: str) -> WorkerRole:
    return "remote" if provider in remote_providers() else "primary"


def worker_providers(role: WorkerRole) -> list[str]:
    if role == "remote" and not remote_providers():
        raise ValueError("WORKER_ROLE=remote requires REMOTE_PROVIDERS")
    return [p for p in SUPPORTED_PROVIDERS if provider_role(p) == role]


def expected_roles() -> list[WorkerRole]:
    return ["primary", "remote"] if remote_providers() else ["primary"]
