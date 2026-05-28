from datetime import datetime, timezone
from typing import Any

from app.faros.runtime.state_store import FarosStateStore


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventLog:
    """Append-only event logger for FAROS runs."""

    def __init__(self, state_store: FarosStateStore):
        self.state_store = state_store

    def info(self, run_id: str, node_id: str, message: str, **details: Any) -> None:
        self.state_store.append_event(
            run_id,
            {
                'timestamp': utc_now_iso(),
                'level': 'info',
                'nodeId': node_id,
                'message': message,
                'details': details,
            },
        )

    def error(self, run_id: str, node_id: str, message: str, **details: Any) -> None:
        self.state_store.append_event(
            run_id,
            {
                'timestamp': utc_now_iso(),
                'level': 'error',
                'nodeId': node_id,
                'message': message,
                'details': details,
            },
        )
