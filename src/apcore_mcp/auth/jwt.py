"""JWT-based authenticator implementation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import jwt as pyjwt
from apcore import Identity

from apcore_mcp.auth.protocol import Authenticator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimMapping:
    """Maps JWT claims to ``Identity`` fields.

    Attributes:
        id_claim: Claim used as ``Identity.id``.
        type_claim: Claim used as ``Identity.type``.
        roles_claim: Claim used as ``Identity.roles`` (expects a list).
        attrs_claims: Extra claims to copy into ``Identity.attrs``.
    """

    id_claim: str = "sub"
    type_claim: str = "type"
    roles_claim: str = "roles"
    attrs_claims: list[str] | None = None


class JWTAuthenticator:
    """Validates JWT Bearer tokens and returns ``Identity``.

    The ``require_auth`` policy (whether unauthenticated requests receive a 401
    or proceed without identity) is owned by :class:`AuthMiddleware`, not by
    this class.  Configure it via ``AuthMiddleware(require_auth=...)``.

    Args:
        key: Secret key or public key for verification.
        algorithms: Allowed JWT algorithms.
        audience: Expected ``aud`` claim (optional).
        issuer: Expected ``iss`` claim (optional).
        claim_mapping: Maps JWT claims to Identity fields.
        require_claims: Claims that must be present in the token.
    """

    def __init__(
        self,
        key: str,
        *,
        algorithms: list[str] | None = None,
        audience: str | None = None,
        issuer: str | None = None,
        claim_mapping: ClaimMapping | None = None,
        require_claims: list[str] | None = None,
    ) -> None:
        self._key = key
        self._algorithms = algorithms or ["HS256"]
        self._audience = audience
        self._issuer = issuer
        self._claim_mapping = claim_mapping or ClaimMapping()
        self._require_claims: list[str] = require_claims if require_claims is not None else ["sub"]

    def authenticate(self, headers: dict[str, str]) -> Identity | None:
        """Extract Bearer token from headers, decode, and return Identity."""
        auth_header = headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return None

        token = auth_header[7:].strip()
        if not token:
            return None

        payload = self._decode_token(token)
        if payload is None:
            return None

        return self._payload_to_identity(payload)

    def _decode_token(self, token: str) -> dict[str, Any] | None:
        """Decode and validate a JWT token. Returns None on any error."""
        try:
            options: dict[str, Any] = {}
            if self._require_claims:
                options["require"] = self._require_claims

            kwargs: dict[str, Any] = {
                "jwt": token,
                "key": self._key,
                "algorithms": self._algorithms,
                "options": options,
                # [JWT-3] Spec mandates a clock-skew leeway of ~30 seconds.
                # pyjwt's default is 0; without this, NTP drift between the
                # token issuer and this server produces spurious 401s on
                # tokens that are valid within ±30s of expiry/nbf.
                "leeway": 30,
            }
            if self._audience is not None:
                kwargs["audience"] = self._audience
            if self._issuer is not None:
                kwargs["issuer"] = self._issuer

            return pyjwt.decode(**kwargs)
        except pyjwt.InvalidTokenError:
            logger.debug("JWT validation failed", exc_info=True)
            return None

    def _payload_to_identity(self, payload: dict[str, Any]) -> Identity | None:
        """Convert a decoded JWT payload to an Identity."""
        mapping = self._claim_mapping
        identity_id = payload.get(mapping.id_claim)
        if identity_id is None:
            return None

        identity_type = payload.get(mapping.type_claim, "user")

        raw_roles = payload.get(mapping.roles_claim)
        roles = tuple(str(r) for r in raw_roles) if isinstance(raw_roles, list) else ()

        attrs: dict[str, Any] = {}
        if mapping.attrs_claims:
            for claim in mapping.attrs_claims:
                if claim in payload:
                    attrs[claim] = payload[claim]

        return Identity(
            id=str(identity_id),
            type=str(identity_type),
            roles=roles,
            attrs=attrs,
        )


# Verify protocol compliance at import time
assert isinstance(JWTAuthenticator.__new__(JWTAuthenticator), Authenticator)
