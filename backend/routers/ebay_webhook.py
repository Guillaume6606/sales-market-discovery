"""eBay marketplace account deletion webhook (production keyset compliance).

eBay marks production keysets "non-compliant" until they either subscribe to
marketplace account deletion notifications or obtain an exemption. This router
implements the subscription path:

- GET  — endpoint-validation handshake: eBay sends ``challenge_code`` and
  expects ``{"challengeResponse": sha256(challengeCode + verificationToken +
  endpointURL)}``. The endpoint URL hashed here must match the URL registered
  in the eBay developer portal byte-for-byte.
- POST — actual deletion notifications, which only require a 200-level ack.

Docs: https://developer.ebay.com/marketplace-account-deletion
"""

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from loguru import logger

from libs.common.settings import settings

router = APIRouter(tags=["webhooks"])


def compute_challenge_response(challenge_code: str, token: str, endpoint_url: str) -> str:
    """SHA-256 over the exact concatenation order mandated by eBay."""
    return hashlib.sha256((challenge_code + token + endpoint_url).encode()).hexdigest()


@router.get("/webhooks/ebay/account-deletion")
def ebay_deletion_challenge(challenge_code: str = Query(...)) -> dict[str, str]:
    """Answer eBay's endpoint-validation handshake."""
    if not settings.ebay_verification_token or not settings.ebay_deletion_endpoint_url:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="EBAY_VERIFICATION_TOKEN / EBAY_DELETION_ENDPOINT_URL not configured",
        )
    return {
        "challengeResponse": compute_challenge_response(
            challenge_code,
            settings.ebay_verification_token,
            settings.ebay_deletion_endpoint_url,
        )
    }


@router.post("/webhooks/ebay/account-deletion")
async def ebay_deletion_notification(request: Request) -> Response:
    """Acknowledge an account deletion notification.

    We persist marketplace listings, not eBay user accounts, so there is no
    per-user data to erase; eBay only requires a 200-level acknowledgment.
    """
    payload: Any = None
    try:
        payload = await request.json()
    except Exception:  # noqa: S110 — malformed body still gets acked, just unlogged
        pass

    username = None
    if isinstance(payload, dict):
        notification = payload.get("notification")
        if isinstance(notification, dict):
            data = notification.get("data")
            if isinstance(data, dict):
                username = data.get("username")

    logger.info("eBay account deletion notification received (username={})", username)
    return Response(status_code=status.HTTP_200_OK)
