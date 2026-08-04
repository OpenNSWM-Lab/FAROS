#!/usr/bin/env python3
"""Small authenticated annotation server for ReviewX human evaluation."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCORE_FIELDS = [
    "humanCorrectness",
    "humanActionability",
    "humanSpecificity",
    "humanGrounding",
    "humanSeverityAgreement",
]
COOKIE_NAME = "reviewx_annotation_session"
ANNOTATOR_RE = re.compile(r"^[\w.-]{1,40}$", re.UNICODE)
BATCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_BODY_BYTES = 64 * 1024
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
COVERAGE_LABELS = {"covered", "partial", "not_covered", "invalid_question", "insufficient_context"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def open_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def initialize_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_db(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                position INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS annotations (
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                annotator_id TEXT NOT NULL,
                human_correctness INTEGER,
                human_actionability INTEGER,
                human_specificity INTEGER,
                human_grounding INTEGER,
                human_severity_agreement INTEGER,
                human_notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (task_id, annotator_id)
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_annotations_annotator
                ON annotations(annotator_id, status);
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                csv_fields_json TEXT NOT NULL,
                task_set_sha256 TEXT NOT NULL,
                imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batch_tasks (
                batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                id TEXT NOT NULL,
                position INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (batch_id, id)
            );
            CREATE TABLE IF NOT EXISTS batch_annotations (
                batch_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                annotator_id TEXT NOT NULL,
                human_correctness INTEGER,
                human_actionability INTEGER,
                human_specificity INTEGER,
                human_grounding INTEGER,
                human_severity_agreement INTEGER,
                human_coverage_label TEXT,
                human_notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (batch_id, task_id, annotator_id),
                FOREIGN KEY (batch_id, task_id)
                    REFERENCES batch_tasks(batch_id, id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_batch_annotations_annotator
                ON batch_annotations(batch_id, annotator_id, status);
            """
        )
        annotation_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(annotations)")
        }
        if "human_coverage_label" not in annotation_columns:
            connection.execute("ALTER TABLE annotations ADD COLUMN human_coverage_label TEXT")
        batch_count = int(connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0])
        if batch_count == 0:
            legacy_tasks = int(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            batch_id = "legacy" if legacy_tasks else "default"
            csv_fields = connection.execute(
                "SELECT value FROM metadata WHERE key='csv_fields'"
            ).fetchone()
            task_set = connection.execute(
                "SELECT value FROM metadata WHERE key='task_set_sha256'"
            ).fetchone()
            now = utc_now()
            connection.execute(
                "INSERT INTO batches(id, name, csv_fields_json, task_set_sha256, imported_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    batch_id, "Migrated legacy batch" if legacy_tasks else "Default batch",
                    str(csv_fields["value"]) if csv_fields else "[]",
                    str(task_set["value"]) if task_set else "", now,
                ),
            )
            if legacy_tasks:
                connection.execute(
                    "INSERT INTO batch_tasks(batch_id, id, position, payload_json, imported_at) "
                    "SELECT ?, id, position, payload_json, imported_at FROM tasks",
                    (batch_id,),
                )
                connection.execute(
                    """
                    INSERT INTO batch_annotations(
                        batch_id, task_id, annotator_id, human_correctness,
                        human_actionability, human_specificity, human_grounding,
                        human_severity_agreement, human_coverage_label, human_notes,
                        status, updated_at
                    )
                    SELECT ?, task_id, annotator_id, human_correctness,
                           human_actionability, human_specificity, human_grounding,
                           human_severity_agreement, human_coverage_label, human_notes,
                           status, updated_at
                    FROM annotations
                    """,
                    (batch_id,),
                )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('active_batch_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (batch_id,),
            )


def active_batch_id(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key='active_batch_id'"
    ).fetchone()
    if not row:
        raise ValueError("no active annotation batch")
    return str(row["value"])


def activate_batch(db_path: Path, batch_id: str) -> None:
    if not BATCH_RE.fullmatch(batch_id):
        raise ValueError("invalid batch ID")
    with open_db(db_path) as connection:
        exists = connection.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not exists:
            raise KeyError(batch_id)
        task_count = int(connection.execute(
            "SELECT COUNT(*) FROM batch_tasks WHERE batch_id=?", (batch_id,)
        ).fetchone()[0])
        if task_count == 0:
            raise ValueError("cannot activate an empty batch")
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('active_batch_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (batch_id,),
        )


def list_batches(db_path: Path) -> list[dict[str, Any]]:
    with open_db(db_path) as connection:
        active = active_batch_id(connection)
        rows = connection.execute(
            """
            SELECT b.*,
                   COUNT(DISTINCT t.id) AS task_count,
                   COUNT(a.task_id) AS annotation_count,
                   SUM(CASE WHEN a.status='completed' THEN 1 ELSE 0 END) AS completed_count,
                   COUNT(DISTINCT a.annotator_id) AS annotator_count
            FROM batches b
            LEFT JOIN batch_tasks t ON t.batch_id=b.id
            LEFT JOIN batch_annotations a ON a.batch_id=t.batch_id AND a.task_id=t.id
            GROUP BY b.id ORDER BY b.imported_at, b.id
            """
        ).fetchall()
    return [{
        "id": row["id"], "name": row["name"], "active": row["id"] == active,
        "taskCount": int(row["task_count"] or 0),
        "annotationCount": int(row["annotation_count"] or 0),
        "completedCount": int(row["completed_count"] or 0),
        "annotatorCount": int(row["annotator_count"] or 0),
        "taskSetSha256": row["task_set_sha256"], "importedAt": row["imported_at"],
    } for row in rows]


def database_status(db_path: Path) -> dict[str, int | str]:
    with open_db(db_path) as connection:
        batch_id = active_batch_id(connection)
        batch = connection.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        task_count = int(connection.execute(
            "SELECT COUNT(*) FROM batch_tasks WHERE batch_id=?", (batch_id,)
        ).fetchone()[0])
        annotation_count = int(connection.execute(
            "SELECT COUNT(*) FROM batch_annotations WHERE batch_id=?", (batch_id,)
        ).fetchone()[0])
        completed_count = int(connection.execute(
            "SELECT COUNT(*) FROM batch_annotations WHERE batch_id=? AND status='completed'",
            (batch_id,),
        ).fetchone()[0])
        annotator_count = int(connection.execute(
            "SELECT COUNT(DISTINCT annotator_id) FROM batch_annotations WHERE batch_id=?",
            (batch_id,),
        ).fetchone()[0])
        batch_count = int(connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0])
    return {
        "activeBatchId": batch_id,
        "activeBatchName": str(batch["name"]),
        "batchCount": batch_count,
        "taskCount": task_count,
        "annotationCount": annotation_count,
        "completedCount": completed_count,
        "annotatorCount": annotator_count,
        "taskSetSha256": str(batch["task_set_sha256"]),
    }


def import_tasks(
    db_path: Path,
    csv_path: Path,
    *,
    replace: bool = False,
    batch_id: str | None = None,
    batch_name: str | None = None,
    activate: bool = False,
) -> int:
    csv_bytes = csv_path.read_bytes()
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if "annotationId" not in fields:
        raise ValueError("task CSV must contain annotationId")
    if not rows:
        raise ValueError("task CSV contains no tasks")
    task_ids = [str(row.get("annotationId") or "").strip() for row in rows]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("task CSV contains duplicate annotationId values")
    if batch_id is not None and not BATCH_RE.fullmatch(batch_id):
        raise ValueError("invalid batch ID")
    now = utc_now()
    with open_db(db_path) as connection:
        target_batch = batch_id or active_batch_id(connection)
        existing_batch = connection.execute(
            "SELECT * FROM batches WHERE id=?", (target_batch,)
        ).fetchone()
        if not existing_batch:
            connection.execute(
                "INSERT INTO batches(id, name, csv_fields_json, task_set_sha256, imported_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    target_batch, batch_name or target_batch,
                    json.dumps(fields, ensure_ascii=False), hashlib.sha256(csv_bytes).hexdigest(), now,
                ),
            )
        annotation_count = int(connection.execute(
            "SELECT COUNT(*) FROM batch_annotations WHERE batch_id=?", (target_batch,)
        ).fetchone()[0])
        if replace and annotation_count:
            raise ValueError(
                f"refusing to replace batch {target_batch} while {annotation_count} annotation rows exist"
            )
        if replace:
            connection.execute("DELETE FROM batch_tasks WHERE batch_id=?", (target_batch,))
        for position, row in enumerate(rows, start=1):
            task_id = str(row.get("annotationId") or "").strip()
            if not task_id:
                raise ValueError(f"task row {position} has no annotationId")
            connection.execute(
                """
                INSERT INTO batch_tasks(batch_id, id, position, payload_json, imported_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(batch_id, id) DO UPDATE SET
                    position=excluded.position,
                    payload_json=excluded.payload_json
                """,
                (target_batch, task_id, position, json.dumps(row, ensure_ascii=False), now),
            )
        connection.execute(
            "UPDATE batches SET name=?, csv_fields_json=?, task_set_sha256=?, imported_at=? WHERE id=?",
            (
                batch_name or (str(existing_batch["name"]) if existing_batch else target_batch),
                json.dumps(fields, ensure_ascii=False), hashlib.sha256(csv_bytes).hexdigest(), now,
                target_batch,
            ),
        )
        if activate:
            connection.execute(
                "UPDATE metadata SET value=? WHERE key='active_batch_id'", (target_batch,)
            )
            if target_batch != "default":
                connection.execute(
                    "DELETE FROM batches WHERE id='default' "
                    "AND NOT EXISTS (SELECT 1 FROM batch_tasks WHERE batch_id='default') "
                    "AND NOT EXISTS (SELECT 1 FROM batch_annotations WHERE batch_id='default')"
                )
    return len(rows)


def annotation_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            **{field: None for field in SCORE_FIELDS},
            "humanCoverageLabel": None,
            "humanNotes": "",
            "status": "unrated",
            "updatedAt": None,
        }
    return {
        "humanCorrectness": row["human_correctness"],
        "humanActionability": row["human_actionability"],
        "humanSpecificity": row["human_specificity"],
        "humanGrounding": row["human_grounding"],
        "humanSeverityAgreement": row["human_severity_agreement"],
        "humanCoverageLabel": row["human_coverage_label"],
        "humanNotes": row["human_notes"],
        "status": row["status"],
        "updatedAt": row["updated_at"],
    }


def task_rows(db_path: Path, annotator_id: str) -> list[dict[str, Any]]:
    with open_db(db_path) as connection:
        batch_id = active_batch_id(connection)
        rows = connection.execute(
            """
            SELECT t.id, t.position, t.payload_json,
                   a.human_correctness, a.human_actionability,
                   a.human_specificity, a.human_grounding,
                   a.human_severity_agreement, a.human_notes,
                   a.human_coverage_label, a.status, a.updated_at
            FROM batch_tasks t
            LEFT JOIN batch_annotations a
              ON a.batch_id=t.batch_id AND a.task_id=t.id AND a.annotator_id=?
            WHERE t.batch_id=?
            ORDER BY t.position
            """,
            (annotator_id, batch_id),
        ).fetchall()
    tasks = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        tasks.append({
            **payload,
            "annotationId": row["id"],
            "batchId": batch_id,
            "position": row["position"],
            "annotation": annotation_dict(row if row["status"] is not None else None),
        })
    return tasks


def save_annotation(db_path: Path, annotator_id: str, data: dict[str, Any]) -> dict[str, Any]:
    task_id = str(data.get("annotationId") or "").strip()
    if not task_id:
        raise ValueError("annotationId is required")
    scores: dict[str, int | None] = {}
    for field in SCORE_FIELDS:
        value = data.get(field)
        if value in {None, ""}:
            scores[field] = None
            continue
        if isinstance(value, bool):
            raise ValueError(f"{field} must be an integer from 1 to 5")
        try:
            score = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be an integer from 1 to 5") from exc
        if score < 1 or score > 5:
            raise ValueError(f"{field} must be an integer from 1 to 5")
        scores[field] = score
    notes = str(data.get("humanNotes") or "").strip()
    if len(notes) > 5000:
        raise ValueError("humanNotes exceeds 5000 characters")
    coverage_label = str(data.get("humanCoverageLabel") or "").strip()
    if coverage_label and coverage_label not in COVERAGE_LABELS:
        raise ValueError("humanCoverageLabel is invalid")
    status = "completed" if (
        all(scores[field] is not None for field in SCORE_FIELDS) and coverage_label
    ) else "draft"
    updated_at = utc_now()
    with open_db(db_path) as connection:
        batch_id = active_batch_id(connection)
        request_batch = str(data.get("batchId") or batch_id)
        if request_batch != batch_id:
            raise ValueError("active batch changed; reload tasks before saving")
        exists = connection.execute(
            "SELECT 1 FROM batch_tasks WHERE batch_id=? AND id=?", (batch_id, task_id)
        ).fetchone()
        if not exists:
            raise KeyError(task_id)
        connection.execute(
            """
            INSERT INTO batch_annotations(
                batch_id, task_id, annotator_id, human_correctness, human_actionability,
                human_specificity, human_grounding, human_severity_agreement,
                human_coverage_label, human_notes, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(batch_id, task_id, annotator_id) DO UPDATE SET
                human_correctness=excluded.human_correctness,
                human_actionability=excluded.human_actionability,
                human_specificity=excluded.human_specificity,
                human_grounding=excluded.human_grounding,
                human_severity_agreement=excluded.human_severity_agreement,
                human_coverage_label=excluded.human_coverage_label,
                human_notes=excluded.human_notes,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                batch_id,
                task_id,
                annotator_id,
                scores["humanCorrectness"],
                scores["humanActionability"],
                scores["humanSpecificity"],
                scores["humanGrounding"],
                scores["humanSeverityAgreement"],
                coverage_label or None,
                notes,
                status,
                updated_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM batch_annotations WHERE batch_id=? AND task_id=? AND annotator_id=?",
            (batch_id, task_id, annotator_id),
        ).fetchone()
    return annotation_dict(row)


def progress(db_path: Path, annotator_id: str) -> dict[str, int]:
    with open_db(db_path) as connection:
        batch_id = active_batch_id(connection)
        total = int(connection.execute(
            "SELECT COUNT(*) FROM batch_tasks WHERE batch_id=?", (batch_id,)
        ).fetchone()[0])
        row = connection.execute(
            """
            SELECT
              SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
              SUM(CASE WHEN status='draft' THEN 1 ELSE 0 END) AS draft
            FROM batch_annotations WHERE batch_id=? AND annotator_id=?
            """,
            (batch_id, annotator_id),
        ).fetchone()
    completed = int(row["completed"] or 0)
    draft = int(row["draft"] or 0)
    return {"total": total, "completed": completed, "draft": draft, "remaining": total - completed}


def export_csv(db_path: Path) -> str:
    with open_db(db_path) as connection:
        batch_id = active_batch_id(connection)
        batch = connection.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        original_fields = json.loads(batch["csv_fields_json"]) if batch else []
        rows = connection.execute(
            """
            SELECT t.payload_json, a.*
            FROM batch_annotations a
            JOIN batch_tasks t ON t.batch_id=a.batch_id AND t.id=a.task_id
            WHERE a.batch_id=?
            ORDER BY a.annotator_id, t.position
            """,
            (batch_id,),
        ).fetchall()
    extra_fields = [
        "batchId", *SCORE_FIELDS, "humanCoverageLabel", "humanNotes",
        "annotatorId", "annotationStatus", "annotationUpdatedAt",
    ]
    fields = list(dict.fromkeys([*original_fields, *extra_fields]))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        payload = json.loads(row["payload_json"])
        payload.update({
            "batchId": batch_id,
            "humanCorrectness": row["human_correctness"] or "",
            "humanActionability": row["human_actionability"] or "",
            "humanSpecificity": row["human_specificity"] or "",
            "humanGrounding": row["human_grounding"] or "",
            "humanSeverityAgreement": row["human_severity_agreement"] or "",
            "humanCoverageLabel": row["human_coverage_label"] or "",
            "humanNotes": row["human_notes"],
            "annotatorId": row["annotator_id"],
            "annotationStatus": row["status"],
            "annotationUpdatedAt": row["updated_at"],
        })
        writer.writerow({field: payload.get(field, "") for field in fields})
    return output.getvalue()


class LoginLimiter:
    def __init__(self) -> None:
        self.attempts: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, address: str) -> bool:
        now = time.time()
        with self.lock:
            queue = self.attempts[address]
            while queue and queue[0] < now - 300:
                queue.popleft()
            if len(queue) >= 10:
                return False
            queue.append(now)
            return True

    def clear(self, address: str) -> None:
        with self.lock:
            self.attempts.pop(address, None)


class AnnotationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        db_path: Path,
        static_dir: Path,
        access_code: str,
        session_secret: str,
        secure_cookie: bool,
    ) -> None:
        super().__init__(address, handler)
        self.db_path = db_path
        self.static_dir = static_dir.resolve()
        self.access_code = access_code
        self.session_secret = session_secret.encode("utf-8")
        self.secure_cookie = secure_cookie
        self.login_limiter = LoginLimiter()


class Handler(BaseHTTPRequestHandler):
    server: AnnotationServer
    server_version = "ReviewXAnnotation/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} [{self.log_date_time_string()}] {format % args}")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        super().end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({"status": "ok", **database_status(self.server.db_path)})
            return
        if path == "/api/me":
            annotator = self._authenticated_annotator()
            if not annotator:
                return
            self._json({"annotatorId": annotator, "progress": progress(self.server.db_path, annotator)})
            return
        if path == "/api/tasks":
            annotator = self._authenticated_annotator()
            if not annotator:
                return
            status = database_status(self.server.db_path)
            self._json({
                "tasks": task_rows(self.server.db_path, annotator),
                "progress": progress(self.server.db_path, annotator),
                "batch": {"id": status["activeBatchId"], "name": status["activeBatchName"]},
            })
            return
        if path == "/api/export.csv":
            if not self._authenticated_annotator():
                return
            content = export_csv(self.server.db_path).encode("utf-8-sig")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="reviewx-human-annotations.csv"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._same_origin():
            self._json({"error": "cross-origin request rejected"}, HTTPStatus.FORBIDDEN)
            return
        if path == "/api/login":
            self._login()
            return
        if path == "/api/logout":
            self._logout()
            return
        if path == "/api/annotations":
            annotator = self._authenticated_annotator()
            if not annotator:
                return
            try:
                result = save_annotation(self.server.db_path, annotator, self._read_json())
            except KeyError:
                self._json({"error": "annotation task not found"}, HTTPStatus.NOT_FOUND)
                return
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._json({"annotation": result, "progress": progress(self.server.db_path, annotator)})
            return
        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _login(self) -> None:
        address = self._client_ip()
        if not self.server.login_limiter.allow(address):
            self._json({"error": "too many login attempts"}, HTTPStatus.TOO_MANY_REQUESTS)
            return
        try:
            data = self._read_json()
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        annotator = str(data.get("annotatorId") or "").strip()
        access_code = str(data.get("accessCode") or "")
        if not ANNOTATOR_RE.fullmatch(annotator):
            self._json({"error": "invalid annotator ID"}, HTTPStatus.BAD_REQUEST)
            return
        if not hmac.compare_digest(access_code, self.server.access_code):
            self._json({"error": "invalid access code"}, HTTPStatus.UNAUTHORIZED)
            return
        self.server.login_limiter.clear(address)
        token = self._create_session(annotator)
        cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL_SECONDS}"
        if self._uses_secure_cookie():
            cookie += "; Secure"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", cookie)
        body = json.dumps({"annotatorId": annotator}, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _client_ip(self) -> str:
        peer = self.client_address[0]
        if peer in {"127.0.0.1", "::1"}:
            forwarded = self.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",", 1)[0].strip()
        return peer

    def _create_session(self, annotator: str) -> str:
        payload = json.dumps(
            {"annotatorId": annotator, "expiresAt": int(time.time()) + SESSION_TTL_SECONDS},
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature = hmac.new(self.server.session_secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def _uses_secure_cookie(self) -> bool:
        forwarded_proto = self.headers.get("X-Forwarded-Proto")
        if forwarded_proto:
            return forwarded_proto.split(",", 1)[0].strip().lower() == "https"
        return self.server.secure_cookie

    def _authenticated_annotator(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            self._json({"error": "invalid session"}, HTTPStatus.UNAUTHORIZED)
            return None
        morsel = cookie.get(COOKIE_NAME)
        if not morsel:
            self._json({"error": "authentication required"}, HTTPStatus.UNAUTHORIZED)
            return None
        try:
            encoded, signature = morsel.value.rsplit(".", 1)
            expected = hmac.new(self.server.session_secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
            if int(payload["expiresAt"]) < int(time.time()):
                raise ValueError("expired")
            annotator = str(payload["annotatorId"])
            if not ANNOTATOR_RE.fullmatch(annotator):
                raise ValueError("annotator")
            return annotator
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._json({"error": "invalid or expired session"}, HTTPStatus.UNAUTHORIZED)
            return None

    def _logout(self) -> None:
        body = b'{"ok":true}'
        self.send_response(HTTPStatus.OK)
        cookie = f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        if self._uses_secure_cookie():
            cookie += "; Secure"
        self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("invalid request body size")
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).netloc == self.headers.get("Host")

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (self.server.static_dir / relative).resolve()
        if self.server.static_dir not in target.parents and target != self.server.static_dir:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        if not target.is_file():
            target = self.server.static_dir / "index.html"
        content = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--db", default="data/annotations.db")
    parser.add_argument("--tasks")
    parser.add_argument("--static-dir", default="static")
    parser.add_argument("--import-only", action="store_true")
    parser.add_argument("--replace-tasks", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--batch-id")
    parser.add_argument("--batch-name")
    parser.add_argument("--activate-batch")
    parser.add_argument("--list-batches", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    static_dir = Path(args.static_dir).resolve()
    initialize_db(db_path)
    if args.tasks:
        count = import_tasks(
            db_path, Path(args.tasks).resolve(), replace=args.replace_tasks,
            batch_id=args.batch_id, batch_name=args.batch_name,
            activate=args.activate_batch == (args.batch_id or ""),
        )
        print(f"importedTasks={count}")
    if args.activate_batch:
        activate_batch(db_path, args.activate_batch)
        print(f"activeBatch={args.activate_batch}")
    if args.list_batches:
        print(json.dumps(list_batches(db_path), ensure_ascii=False))
        return 0
    if args.status_only:
        print(json.dumps(database_status(db_path), ensure_ascii=False))
        return 0
    if args.import_only:
        return 0
    if not static_dir.is_dir():
        raise SystemExit(f"static directory not found: {static_dir}")

    access_code = os.getenv("ANNOTATION_ACCESS_CODE")
    session_secret = os.getenv("ANNOTATION_SESSION_SECRET")
    if not access_code or len(access_code) < 12:
        raise SystemExit("ANNOTATION_ACCESS_CODE must contain at least 12 characters")
    if not session_secret or len(session_secret) < 32:
        raise SystemExit("ANNOTATION_SESSION_SECRET must contain at least 32 characters")
    server = AnnotationServer(
        (args.host, args.port),
        Handler,
        db_path=db_path,
        static_dir=static_dir,
        access_code=access_code,
        session_secret=session_secret,
        secure_cookie=os.getenv("ANNOTATION_COOKIE_SECURE", "0") == "1",
    )
    print(f"listening=http://{args.host}:{args.port} db={db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
