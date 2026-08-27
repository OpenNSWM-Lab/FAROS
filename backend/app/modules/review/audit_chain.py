"""Append-only hash chains for ReviewX human decisions."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List


GENESIS = "sha256:" + ("0" * 64)


def _event_hash(event: Dict[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "eventHash"}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def seal_history(history: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sealed: List[Dict[str, Any]] = []
    previous = GENESIS
    for raw in history:
        event = {key: value for key, value in dict(raw).items() if key not in {"previousEventHash", "eventHash"}}
        event["previousEventHash"] = previous
        event["eventHash"] = _event_hash(event)
        sealed.append(event)
        previous = event["eventHash"]
    return sealed


def append_event(history: Iterable[Dict[str, Any]], event: Dict[str, Any]) -> List[Dict[str, Any]]:
    sealed = seal_history(history)
    item = dict(event)
    item["previousEventHash"] = sealed[-1]["eventHash"] if sealed else GENESIS
    item["eventHash"] = _event_hash(item)
    return [*sealed, item]


def verify_history(history: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    previous = GENESIS
    count = 0
    for index, raw in enumerate(history):
        event = dict(raw)
        if event.get("previousEventHash") != previous or event.get("eventHash") != _event_hash(event):
            return {"valid": False, "eventCount": count, "invalidIndex": index}
        previous = str(event["eventHash"])
        count += 1
    return {"valid": True, "eventCount": count, "headHash": previous}


def record_audit_integrity(record: Dict[str, Any]) -> Dict[str, Any]:
    streams: Dict[str, Any] = {}
    for stage, item in (record.get("humanSignoffs") or {}).items():
        streams[f"signoff:{stage}"] = verify_history((item or {}).get("history") or [])
    for condition_id, item in (record.get("humanFeedbackVerifications") or {}).items():
        streams[f"condition:{condition_id}"] = verify_history((item or {}).get("history") or [])
    invalid = [name for name, state in streams.items() if not state["valid"]]
    return {
        "valid": not invalid,
        "streamCount": len(streams),
        "eventCount": sum(state["eventCount"] for state in streams.values()),
        "invalidStreams": invalid,
        "streams": streams,
    }
