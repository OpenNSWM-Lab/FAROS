"""Optional bearer-token identity enforcement for ReviewX human decisions."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from app.core.user_context import get_current_user_id, get_current_user_role


STAGE_ROLES = {
    "plan": {"team_lead", "domain_expert", "safety_reviewer"},
    "repair": {"team_lead", "domain_expert", "safety_reviewer"},
    "conclusion": {"team_lead", "domain_expert"},
    "condition": {"team_lead", "domain_expert", "safety_reviewer"},
}


class ReviewAuthenticationError(PermissionError):
    """Missing or invalid bearer credentials."""


class ReviewAuthorizationError(PermissionError):
    """Authenticated or declared identity lacks the requested authority."""


def _required() -> bool:
    return os.getenv("FAROS_REVIEWX_REQUIRE_AUTH", "false").strip().lower() in {"1", "true", "yes", "on"}


def reviewx_auth_mode() -> str:
    """Return the explicit ReviewX identity mode.

    ``legacy`` preserves existing API clients until deployments opt into the
    proxy contract.  The production environment template uses ``proxy``.
    """

    configured = os.getenv("FAROS_REVIEWX_AUTH_MODE", "").strip().lower()
    if configured:
        if configured not in {"proxy", "bearer", "local", "legacy"}:
            raise ValueError("FAROS_REVIEWX_AUTH_MODE must be proxy, bearer, local, or legacy")
        return configured
    return "bearer" if _required() else "legacy"


def _signer_users() -> set[str]:
    return {
        item.strip()
        for item in os.getenv("FAROS_REVIEWX_SIGNER_USERS", "").split(",")
        if item.strip()
    }


def ensure_reviewx_write_access() -> Dict[str, str]:
    """Reject read-only accounts while allowing team evidence preparation."""

    actor_id = get_current_user_id()
    actor_role = get_current_user_role(actor_id)
    if actor_role == "judge":
        raise ReviewAuthorizationError(
            "Judge accounts are read-only and cannot modify shared ReviewX evidence"
        )
    mode = reviewx_auth_mode()
    return {"actorAccountId": actor_id, "actorRole": actor_role, "authMode": mode}


def stored_actor_is_authorized(actor_id: str) -> bool:
    """Validate an actor already sealed into a proxy-mode audit event."""

    if reviewx_auth_mode() != "proxy":
        return True
    return bool(actor_id) and actor_id in _signer_users()


def _principals() -> Dict[str, Dict[str, Any]]:
    raw = os.getenv("FAROS_REVIEWX_AUTH_TOKENS", "").strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("FAROS_REVIEWX_AUTH_TOKENS must be a JSON object")
    return {str(token): dict(principal) for token, principal in payload.items()}


def authorize_reviewer(
    *,
    stage: str,
    reviewer_role: str,
    reviewer_id: str,
    authorization: Optional[str] = None,
    technical_test: bool = False,
) -> Dict[str, Any]:
    allowed = set(STAGE_ROLES.get(stage) or set())
    if technical_test:
        allowed.add("technical_tester")
    if reviewer_role not in allowed:
        raise ReviewAuthorizationError(f"Role '{reviewer_role}' is not permitted for {stage} review")
    actor = ensure_reviewx_write_access()
    mode = actor["authMode"]
    if mode == "proxy":
        signers = _signer_users()
        if not signers or actor["actorAccountId"] not in signers:
            raise ReviewAuthorizationError(
                "The authenticated account is not in FAROS_REVIEWX_SIGNER_USERS"
            )
        return {
            **actor,
            "assurance": "trusted_proxy_basic_auth",
            "authAssurance": "trusted_proxy_basic_auth",
            "reviewerId": reviewer_id,
            "roles": [reviewer_role],
        }
    if mode == "local":
        return {
            **actor,
            "assurance": "local_test",
            "authAssurance": "local_test",
            "reviewerId": reviewer_id,
            "roles": [reviewer_role],
        }
    principals = _principals()
    token = ""
    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        if mode == "bearer" or _required():
            raise ReviewAuthenticationError("ReviewX bearer authentication is required")
        return {
            **actor,
            "assurance": "self_reported",
            "authAssurance": "self_reported",
            "reviewerId": reviewer_id,
            "roles": [reviewer_role],
        }
    principal = principals.get(token)
    if principal is None:
        raise ReviewAuthenticationError("Invalid ReviewX bearer token")
    if str(principal.get("id") or "") != reviewer_id:
        raise ReviewAuthorizationError("Authenticated identity does not match reviewerId")
    roles = {str(role) for role in principal.get("roles") or []}
    if reviewer_role not in roles:
        raise ReviewAuthorizationError("Authenticated identity does not hold the requested reviewer role")
    return {
        "actorAccountId": str(principal.get("id") or ""),
        "actorRole": "api_reviewer",
        "authMode": "bearer",
        "assurance": "bearer_token",
        "authAssurance": "bearer_token",
        "reviewerId": reviewer_id,
        "roles": sorted(roles),
    }
