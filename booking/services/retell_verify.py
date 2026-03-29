"""
Retell `x-retell-signature` verification.

The official Retell Python SDK exposes `Retell.verify(body, api_key, signature)`.
Always verify against the **raw** request bytes, not a re-serialized JSON object.

If you cannot use the SDK, replace `verify_retell_request` with an implementation
that matches Retell's documented algorithm (see Retell "Secure the webhook" docs).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def verify_retell_request(raw_body: str, signature: str | None, api_key: str) -> bool:
    """
    Return True if the Retell signature is valid.

    :param raw_body: Exact UTF-8 decoded body string Retell signed.
    :param signature: Value of the ``x-retell-signature`` header (may be None).
    :param api_key: Retell API key with the webhook badge (same key used in dashboard).
    """
    if not api_key:
        logger.warning("RETELL_API_KEY is not configured; rejecting request")
        return False
    if not signature:
        return False
    try:
        from retell import Retell

        client = Retell(api_key=api_key)
        return bool(client.verify(raw_body, api_key, signature))
    except Exception:
        logger.exception("Retell SDK verify failed")
        return False
