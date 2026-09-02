"""Request-scoped user identity and subprocess environment hygiene."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar, Token, copy_context
from typing import Any, Callable, Iterator, Optional, TypeVar


_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_current_user_id: ContextVar[Optional[str]] = ContextVar(
    "faros_current_user_id",
    default=None,
)
T = TypeVar("T")


def normalize_user_id(value: Optional[str], *, fallback: Optional[str] = None) -> str:
    """Normalize a trusted proxy identity into a storage-safe identifier."""
    candidate = (value or fallback or "").strip()
    if not candidate:
        candidate = os.getenv("FAROS_DEFAULT_USER", "local").strip() or "local"
    if not _USER_ID_PATTERN.fullmatch(candidate):
        raise ValueError("Invalid FAROS user identifier")
    return candidate


def get_current_user_id() -> str:
    return normalize_user_id(_current_user_id.get())


def set_current_user_id(user_id: Optional[str]) -> Token:
    return _current_user_id.set(normalize_user_id(user_id))


def reset_current_user_id(token: Token) -> None:
    _current_user_id.reset(token)


@contextmanager
def use_user(user_id: str) -> Iterator[str]:
    """Temporarily bind a user, primarily for jobs and tests."""
    token = set_current_user_id(user_id)
    try:
        yield get_current_user_id()
    finally:
        reset_current_user_id(token)


def get_current_user_role(user_id: Optional[str] = None) -> str:
    resolved = normalize_user_id(user_id, fallback=get_current_user_id())
    reviewer_users = {
        item.strip()
        for item in os.getenv(
            "FAROS_REVIEWER_USERS",
            os.getenv("FAROS_REVIEWX_SIGNER_USERS", ""),
        ).split(",")
        if item.strip()
    }
    team_users = {
        item.strip()
        for item in os.getenv("FAROS_TEAM_USERS", "faros-team,team,local").split(",")
        if item.strip()
    }
    judge_users = {
        item.strip()
        for item in os.getenv("FAROS_JUDGE_USERS", "faros-judge,judge").split(",")
        if item.strip()
    }
    if resolved in reviewer_users:
        return "reviewer"
    if resolved in team_users:
        return "team"
    if resolved in judge_users:
        return "judge"
    return "user"


def call_with_current_context(fn: Callable[..., T], *args: Any, **kwargs: Any) -> Callable[..., T]:
    """Capture the caller's context for one later thread-pool invocation."""
    context = copy_context()

    def _bound(*call_args: Any, **call_kwargs: Any) -> T:
        merged_kwargs = {**kwargs, **call_kwargs}
        return context.run(fn, *args, *call_args, **merged_kwargs)

    return _bound


_SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "AUTH_TOKEN",
    "ACCESS_TOKEN",
    "REFRESH_TOKEN",
    "PRIVATE_KEY",
    "PASSWORD",
    "CREDENTIAL",
    "CLIENT_SECRET",
)
_SENSITIVE_ENV_EXACT = {
    "FAROS_CREDENTIAL_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "TOKEN",
}


def sanitized_subprocess_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Return the process environment without application or provider secrets."""
    sanitized = {}
    for name, value in os.environ.items():
        upper_name = name.upper()
        if upper_name in _SENSITIVE_ENV_EXACT:
            continue
        if any(marker in upper_name for marker in _SENSITIVE_ENV_MARKERS):
            continue
        sanitized[name] = value
    if extra:
        sanitized.update({str(name): str(value) for name, value in extra.items()})
    return sanitized
