"""Cloudflare Access identity verification (security review 2026-11-01).

The public site sits behind Cloudflare Access. Every request Cloudflare forwards carries a
``Cf-Access-Jwt-Assertion`` header: an RS256 JWT signed by the team's Access keys whose ``email`` claim is the
identity that passed the email-PIN gate. Registration uses it to prove the caller owns the invited email.

Rules:

* Only the signed assertion is trusted. The plain ``Cf-Access-Authenticated-User-Email`` header is NEVER read:
  anything that reaches the origin without Cloudflare (or a local process) could set it.
* Signature (RS256 only), audience (the AISP Access application AUD tag), issuer (the team domain), ``exp`` and
  ``iat`` are all verified.
* The team JWKS is fetched from ``cf_access_certs_url`` and cached for about an hour.
* Everything fails CLOSED: a missing header, a network error fetching the keys, a missing crypto backend or any
  claim problem yields ``None`` (no verified identity), never an exception the caller could mistake for success.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import jwt

ASSERTION_HEADER = "Cf-Access-Jwt-Assertion"
#: Algorithms accepted for the assertion. Cloudflare Access signs with RS256; never accept "none" or HMAC.
ALGORITHMS: list[str] = ["RS256"]
JWKS_CACHE_SECONDS = 3600
JWKS_FETCH_TIMEOUT_SECONDS = 5
LEEWAY_SECONDS = 30

logger = logging.getLogger(__name__)

_clients: dict[str, jwt.PyJWKClient] = {}
_clients_lock = threading.Lock()


def _jwks_client(certs_url: str) -> jwt.PyJWKClient:
    with _clients_lock:
        client = _clients.get(certs_url)
        if client is None:
            client = jwt.PyJWKClient(
                certs_url,
                cache_keys=True,
                cache_jwk_set=True,
                lifespan=JWKS_CACHE_SECONDS,
                timeout=JWKS_FETCH_TIMEOUT_SECONDS,
            )
            _clients[certs_url] = client
        return client


def _signing_key(token: str, settings: Any) -> Any:
    return _jwks_client(settings.cf_access_certs_url).get_signing_key_from_jwt(token).key


def reset_cache() -> None:
    """Drop cached JWKS clients (tests, or after a key-rotation incident)."""
    with _clients_lock:
        _clients.clear()


def verified_access_email(token: str | None, settings: Any) -> str | None:
    """Return the lowercased email of a valid Access assertion, or ``None``. Never raises."""

    if not token or not isinstance(token, str):
        return None
    audience = (getattr(settings, "cf_access_aud", "") or "").strip()
    issuer = (getattr(settings, "cf_access_issuer", "") or "").strip()
    if not audience or not issuer or not getattr(settings, "cf_access_certs_url", ""):
        return None
    try:
        key = _signing_key(token, settings)
        claims = jwt.decode(
            token,
            key,
            algorithms=ALGORITHMS,
            audience=audience,
            issuer=issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except Exception as exc:  # noqa: BLE001 - fail closed on every error, including JWKS/network/crypto
        logger.info("Cloudflare Access assertion rejected: %s", type(exc).__name__)
        return None
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        return None
    return email.strip().lower()


__all__ = ["ASSERTION_HEADER", "reset_cache", "verified_access_email"]
