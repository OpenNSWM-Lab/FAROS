"""Optional bearer-token identity enforcement for ReviewX human decisions."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


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
    principals = _principals()
    token = ""
    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        if _required():
            raise ReviewAuthenticationError("ReviewX bearer authentication is required")
        return {"assurance": "self_reported", "reviewerId": reviewer_id, "roles": [reviewer_role]}
    principal = principals.get(token)
    if principal is None:
        raise ReviewAuthenticationError("Invalid ReviewX bearer token")
    if str(principal.get("id") or "") != reviewer_id:
        raise ReviewAuthorizationError("Authenticated identity does not match reviewerId")
    roles = {str(role) for role in principal.get("roles") or []}
    if reviewer_role not in roles:
        raise ReviewAuthorizationError("Authenticated identity does not hold the requested reviewer role")
    return {"assurance": "bearer_token", "reviewerId": reviewer_id, "roles": sorted(roles)}
