"""Deterministic project-closure control plane.

The model may propose work, evidence, and blocker classifications.  This
module owns the durable state machine, budgets, mutation reservations,
independent verification, and tamper-evident receipts.  It deliberately does
not execute external effects; callers must reserve an effect here before an
adapter performs it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

try:
    from fastapi import APIRouter, Header, HTTPException, Request
except ModuleNotFoundError:  # Core-only verification in minimal environments.
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: Any):
            super().__init__(str(detail))
            self.status_code = status_code
            self.detail = detail

    class APIRouter:  # pragma: no cover - real FastAPI supplies the runtime router.
        def __init__(self, *args: Any, **kwargs: Any):
            pass

        def get(self, *args: Any, **kwargs: Any):
            return lambda function: function

        def post(self, *args: Any, **kwargs: Any):
            return lambda function: function

    class Request:  # pragma: no cover - typing shim for dependency-light tests.
        client: Any = None

    def Header(default: Any = None, **_kwargs: Any) -> Any:  # pragma: no cover
        return default
from pydantic import BaseModel, Field, field_validator, model_validator


ACTION_STATES = frozenset(
    {
        "READY",
        "RUNNING",
        "VERIFYING",
        "PASSED",
        "FAILED_RETRYABLE",
        "LOCAL_BLOCKED",
        "APPROVAL_PENDING",
        "QUARANTINED",
    }
)
PROJECT_STATES = frozenset(
    {
        "READY",
        "RUNNING",
        "VERIFYING",
        "PASSED",
        "LOCAL_BLOCKED",
        "APPROVAL_PENDING",
        "QUARANTINED",
    }
)
EFFECT_STATES = frozenset({"RESERVED", "DISPATCHED", "COMPLETED", "FAILED", "UNKNOWN"})
CLOSURE_SCHEMA_VERSION = 2
OBSTACLE_CLASSES = frozenset(
    {
        "HARD BLOCKER",
        "LOCAL BLOCKER",
        "UNCERTAINTY",
        "VERIFICATION FAILURE",
        "APPROVAL GATE",
        "NO BLOCKER",
    }
)
HARD_REASONS = frozenset(
    {
        "unavailable_authority",
        "irreversible_approval",
        "dependency_without_substitute",
        "safety_boundary",
        "irreversible_choice",
    }
)
RUNNABLE_STATES = frozenset({"READY", "FAILED_RETRYABLE"})
TERMINAL_STATES = frozenset({"PASSED", "QUARANTINED"})

_SECRET_KEY = re.compile(r"(?i)(secret|password|token|api[_-]?key|credential)")
_SECRET_TEXT = re.compile(
    r"(?i)((?:secret|password|token|api[_-]?key|credential)\s*[:=]\s*)\S+"
)
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sanitize(value: Any, key: str = "") -> Any:
    """Redact likely credentials before anything reaches durable storage."""
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        value = _SECRET_TEXT.sub(r"\1[REDACTED]", value)
        return _OPENAI_STYLE_KEY.sub("[REDACTED]", value)
    return value


def _reject_secret_shaped_identifier(value: str | None) -> str | None:
    if value is not None and _sanitize(value) != value:
        raise ValueError("identity and idempotency fields must not contain secret-shaped text")
    return value


class ClosureError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class EvidenceItem(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    digest: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    summary: str = Field(min_length=1, max_length=1000)


class ActionSpec(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    success_criteria: list[str] = Field(min_length=1, max_length=20)
    value: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    probability: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    remaining_effort: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    max_attempts: int = Field(default=3, ge=1, le=20)
    risk: Literal["read_only", "reversible", "irreversible"] = "reversible"
    requires_approval: bool = False

    @model_validator(mode="after")
    def irreversible_requires_approval(self):
        if self.risk == "irreversible":
            self.requires_approval = True
        if self.risk == "read_only" and self.requires_approval:
            raise ValueError(
                "read_only actions cannot require an external-effect approval"
            )
        return self


class ProjectCreate(BaseModel):
    request_key: str | None = Field(default=None, min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=2000)
    closed_when: list[str] = Field(min_length=1, max_length=30)
    proposer_id: str = Field(min_length=1, max_length=200)
    ratifier_id: str = Field(min_length=1, max_length=200)
    actions: list[ActionSpec] = Field(min_length=1, max_length=100)
    max_attempts: int = Field(default=20, ge=1, le=1000)
    max_effects: int = Field(default=20, ge=0, le=1000)

    _safe_identities = field_validator(
        "request_key", "proposer_id", "ratifier_id"
    )(_reject_secret_shaped_identifier)

    @model_validator(mode="after")
    def separation_of_duties(self):
        if self.proposer_id == self.ratifier_id:
            raise ValueError("proposer_id and ratifier_id must be different")
        if self.max_attempts < len(self.actions):
            raise ValueError("max_attempts must allow at least one attempt per required action")
        approval_actions = sum(action.requires_approval for action in self.actions)
        if self.max_effects < approval_actions:
            raise ValueError(
                "max_effects must allow at least one effect per approval-required action"
            )
        return self


class ClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=200)
    claim_key: str = Field(min_length=1, max_length=300)
    approval_ref: str | None = Field(default=None, max_length=500)
    resolution_evidence: list[EvidenceItem] = Field(default_factory=list, max_length=20)

    _safe_identities = field_validator(
        "worker_id", "claim_key", "approval_ref"
    )(_reject_secret_shaped_identifier)


class ResultRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=200)
    attempt_key: str = Field(min_length=1, max_length=300)
    outcome: Literal["success", "failed", "blocked"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=30)
    obstacle_class: str | None = None
    hard_reason: str | None = None
    next_step: str | None = Field(default=None, max_length=1000)

    _safe_identities = field_validator(
        "worker_id", "attempt_key"
    )(_reject_secret_shaped_identifier)

    @field_validator("obstacle_class")
    @classmethod
    def known_obstacle_class(cls, value: str | None) -> str | None:
        if value is not None and value not in OBSTACLE_CLASSES:
            raise ValueError(f"obstacle_class must be one of {sorted(OBSTACLE_CLASSES)}")
        return value


class VerifyRequest(BaseModel):
    verifier_id: str = Field(min_length=1, max_length=200)
    attempt_key: str = Field(min_length=1, max_length=300)
    result_receipt_id: str = Field(min_length=1, max_length=200)
    verdict: Literal["pass", "fail"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=30)

    _safe_identities = field_validator(
        "verifier_id", "attempt_key", "result_receipt_id"
    )(_reject_secret_shaped_identifier)


class EffectRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=300)
    attempt_key: str = Field(min_length=1, max_length=300)
    effect_kind: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=1000)
    payload_digest: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    approval_ref: str | None = Field(default=None, max_length=500)

    _safe_identities = field_validator(
        "idempotency_key", "attempt_key", "effect_kind", "target", "approval_ref"
    )(_reject_secret_shaped_identifier)


class EffectStatusRequest(BaseModel):
    adapter_id: str = Field(min_length=1, max_length=200)
    status: Literal["DISPATCHED", "COMPLETED", "FAILED", "UNKNOWN"]
    result_digest: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    dispatch_token: str | None = Field(default=None, min_length=8, max_length=200)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=30)

    _safe_identities = field_validator(
        "adapter_id"
    )(_reject_secret_shaped_identifier)


class EffectReconcileRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=200)
    authorization_ref: str = Field(min_length=1, max_length=500)
    decision: Literal["COMPLETED", "FAILED"]
    result_digest: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=30)

    _safe_identities = field_validator(
        "operator_id", "authorization_ref"
    )(_reject_secret_shaped_identifier)


class HaltRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)

    _safe_identities = field_validator(
        "operator_id"
    )(_reject_secret_shaped_identifier)


class ClosureStore:
    """SQLite/WAL state owner for the project-closure protocol."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

    @classmethod
    def _ensure_column(
        cls, conn: sqlite3.Connection, table: str, name: str, definition: str
    ) -> bool:
        if name in cls._columns(conn, table):
            return False
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        return True

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Upgrade prior closure prototypes without trusting unbound legacy state."""
        meta_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'closure_schema_meta'"
        ).fetchone()
        if meta_exists:
            existing_meta = conn.execute(
                "SELECT version FROM closure_schema_meta WHERE singleton = 1"
            ).fetchone()
            if existing_meta and int(existing_meta["version"]) > CLOSURE_SCHEMA_VERSION:
                raise ClosureError(
                    "schema_too_new",
                    "Closure database schema is newer than this runtime supports",
                    503,
                )
        # The prototype index prevented a safely failed effect from being retried
        # on a later attempt.  The attempt-bound index is created after backfill.
        conn.execute("DROP INDEX IF EXISTS idx_effect_action_digest")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS closure_schema_meta (
                   singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                   version INTEGER NOT NULL,
                   migrated_at TEXT NOT NULL
               )"""
        )

        self._ensure_column(conn, "project_requests", "request_digest", "TEXT")
        self._ensure_column(conn, "actions", "attempt_key", "TEXT")
        self._ensure_column(conn, "actions", "result_receipt_id", "TEXT")

        effect_columns_before = self._columns(conn, "effect_reservations")
        legacy_effect_state = "status" not in effect_columns_before
        self._ensure_column(conn, "effect_reservations", "attempt_key", "TEXT")
        self._ensure_column(conn, "effect_reservations", "effect_digest", "TEXT")
        self._ensure_column(conn, "effect_reservations", "approval_ref", "TEXT")
        self._ensure_column(
            conn, "effect_reservations", "status", "TEXT NOT NULL DEFAULT 'RESERVED'"
        )
        self._ensure_column(conn, "effect_reservations", "adapter_id", "TEXT")
        self._ensure_column(conn, "effect_reservations", "result_digest", "TEXT")
        self._ensure_column(conn, "effect_reservations", "dispatch_token", "TEXT")
        self._ensure_column(conn, "effect_reservations", "receipt_id", "TEXT")
        self._ensure_column(conn, "effect_reservations", "created_at", "TEXT")
        self._ensure_column(conn, "effect_reservations", "updated_at", "TEXT")

        # Recover attempt bindings from the authoritative claim table when possible.
        conn.execute(
            """UPDATE actions SET attempt_key = (
                   SELECT claim_key FROM claims
                   WHERE claims.action_id = actions.id
                   ORDER BY claims.created_at DESC LIMIT 1
               ) WHERE attempt_key IS NULL"""
        )
        timestamp = _now()
        conn.execute(
            """UPDATE effect_reservations SET attempt_key = COALESCE(
                   (SELECT attempt_key FROM actions WHERE actions.id = effect_reservations.action_id),
                   'legacy:' || idempotency_key
               ) WHERE attempt_key IS NULL OR attempt_key = ''"""
        )
        for row in conn.execute(
            "SELECT idempotency_key FROM effect_reservations WHERE effect_digest IS NULL OR effect_digest = ''"
        ).fetchall():
            digest = hashlib.sha256(f"legacy:{row['idempotency_key']}".encode()).hexdigest()
            conn.execute(
                "UPDATE effect_reservations SET effect_digest = ? WHERE idempotency_key = ?",
                (digest, row["idempotency_key"]),
            )
        conn.execute(
            """UPDATE effect_reservations
               SET created_at = COALESCE(created_at, ?),
                   updated_at = COALESCE(updated_at, created_at, ?)""",
            (timestamp, timestamp),
        )

        # A pre-lifecycle reservation has uncertain external outcome.  Never redrive it.
        if legacy_effect_state:
            legacy_digest = hashlib.sha256(b"legacy-effect-outcome-unknown").hexdigest()
            conn.execute(
                """UPDATE effect_reservations
                   SET status = 'UNKNOWN', result_digest = COALESCE(result_digest, ?),
                       updated_at = ?""",
                (legacy_digest, timestamp),
            )

        receipt_rows = list(conn.execute("SELECT * FROM receipts ORDER BY seq"))
        parsed_receipts: list[tuple[sqlite3.Row, dict[str, Any]]] = []
        for receipt in receipt_rows:
            try:
                parsed_receipts.append((receipt, json.loads(receipt["payload_json"])))
            except (TypeError, json.JSONDecodeError):
                continue

        parsed_by_id = {receipt["id"]: (receipt, payload) for receipt, payload in parsed_receipts}

        for project in conn.execute("SELECT * FROM projects").fetchall():
            contracts = [
                (receipt, payload)
                for receipt, payload in parsed_receipts
                if receipt["project_id"] == project["id"]
                and receipt["kind"] in {"contract.ratified", "schema.contract_bound"}
            ]
            if not contracts or contracts[-1][1].get("actions"):
                continue
            source_receipt, source_payload = contracts[-1]
            action_rows = conn.execute(
                "SELECT * FROM actions WHERE project_id = ? ORDER BY rowid", (project["id"],)
            ).fetchall()
            current_action_ids = [action["id"] for action in action_rows]
            source_action_ids = source_payload.get("action_ids")
            ratified_action_ids = (
                [str(action_id) for action_id in source_action_ids]
                if isinstance(source_action_ids, list) and source_action_ids
                else current_action_ids
            )
            source_budgets = source_payload.get("budgets")
            if not isinstance(source_budgets, dict):
                source_budgets = {}
            bound_name = source_payload.get("name", project["name"])
            bound_objective = source_payload.get("objective", project["objective"])
            bound_closed_when = source_payload.get(
                "closed_when", json.loads(project["closed_when_json"])
            )
            bound_proposer = source_payload.get("proposer_id", project["proposer_id"])
            bound_ratifier = source_payload.get("ratifier_id", project["ratifier_id"])
            bound_max_attempts = source_budgets.get("max_attempts", project["max_attempts"])
            bound_max_effects = source_budgets.get("max_effects", project["max_effects"])
            project_contract_mismatches = [
                field
                for field, current, ratified in (
                    ("name", project["name"], bound_name),
                    ("objective", project["objective"], bound_objective),
                    (
                        "closed_when",
                        json.loads(project["closed_when_json"]),
                        bound_closed_when,
                    ),
                    ("proposer_id", project["proposer_id"], bound_proposer),
                    ("ratifier_id", project["ratifier_id"], bound_ratifier),
                    ("max_attempts", project["max_attempts"], bound_max_attempts),
                    ("max_effects", project["max_effects"], bound_max_effects),
                )
                if current != ratified
            ]
            self._append_receipt(
                conn,
                project["id"],
                "schema.contract_bound",
                {
                    "name": bound_name,
                    "objective": bound_objective,
                    "closed_when": bound_closed_when,
                    "proposer_id": bound_proposer,
                    "ratifier_id": bound_ratifier,
                    # Never let mutable legacy rows narrow an already-ratified set.
                    "action_ids": ratified_action_ids,
                    "actions": [
                        {
                            "id": action["id"],
                            "title": action["title"],
                            "success_criteria": json.loads(action["success_criteria_json"]),
                            "value": action["value"],
                            "probability": action["probability"],
                            "remaining_effort": action["remaining_effort"],
                            "max_attempts": action["max_attempts"],
                            "risk": action["risk"],
                            "requires_approval": bool(action["requires_approval"]),
                        }
                        for action in action_rows
                    ],
                    "budgets": {
                        "max_attempts": bound_max_attempts,
                        "max_effects": bound_max_effects,
                    },
                    "source_receipt_id": source_receipt["id"],
                    "source_action_ids": source_action_ids or [],
                    "schema_version": CLOSURE_SCHEMA_VERSION,
                },
            )
            if project_contract_mismatches:
                self._append_receipt(
                    conn,
                    project["id"],
                    "contract.integrity_violation",
                    {
                        "fields": project_contract_mismatches,
                        "reason": "legacy_projection_disagrees_with_ratified_contract",
                        "source_receipt_id": source_receipt["id"],
                    },
                )
            if project_contract_mismatches or set(ratified_action_ids) != set(
                current_action_ids
            ):
                self._sync_project_status(conn, project["id"])

        # V0 claim receipts used `claim_key` rather than `attempt_key`.  Keep the
        # original receipt and point the mutable claim row at an explicit binding.
        for claim in conn.execute("SELECT * FROM claims").fetchall():
            source = parsed_by_id.get(claim["receipt_id"])
            is_current_binding = bool(
                source
                and source[0]["kind"] in {"action.claimed", "schema.claim_bound"}
                and source[1].get("action_id") == claim["action_id"]
                and source[1].get("attempt_key") == claim["claim_key"]
                and source[1].get("worker_id") == claim["worker_id"]
            )
            if is_current_binding:
                continue
            action = conn.execute(
                "SELECT project_id FROM actions WHERE id = ?", (claim["action_id"],)
            ).fetchone()
            if action is None:
                continue
            binding = self._append_receipt(
                conn,
                action["project_id"],
                "schema.claim_bound",
                {
                    "action_id": claim["action_id"],
                    "attempt_key": claim["claim_key"],
                    "worker_id": claim["worker_id"],
                    "source_receipt_id": claim["receipt_id"],
                    "schema_version": CLOSURE_SCHEMA_VERSION,
                },
            )
            conn.execute(
                "UPDATE claims SET receipt_id = ? WHERE claim_key = ?",
                (binding["id"], claim["claim_key"]),
            )

        # Bind current results when an older receipt already carries enough identity.
        for action in conn.execute(
            """SELECT * FROM actions
               WHERE result_receipt_id IS NULL AND status IN ('VERIFYING', 'PASSED')"""
        ).fetchall():
            candidates = [
                (receipt, payload)
                for receipt, payload in parsed_receipts
                if receipt["project_id"] == action["project_id"]
                and receipt["kind"] == "worker.result"
                and payload.get("action_id") == action["id"]
                and (
                    payload.get("attempt_key") is None
                    or payload.get("attempt_key") == action["attempt_key"]
                )
            ]
            if candidates:
                source_receipt, source_payload = candidates[-1]
                if source_payload.get("attempt_key") == action["attempt_key"]:
                    result_receipt_id = source_receipt["id"]
                else:
                    binding = self._append_receipt(
                        conn,
                        action["project_id"],
                        "schema.result_bound",
                        {
                            "action_id": action["id"],
                            "attempt_key": action["attempt_key"],
                            "worker_id": action["worker_id"],
                            "source_receipt_id": source_receipt["id"],
                            "outcome": source_payload.get("outcome"),
                            "schema_version": CLOSURE_SCHEMA_VERSION,
                        },
                    )
                    result_receipt_id = binding["id"]
                conn.execute(
                    "UPDATE actions SET result_receipt_id = ? WHERE id = ?",
                    (result_receipt_id, action["id"]),
                )

        # Preserve an old independently-evidenced pass only by appending an
        # explicit binding to its current attempt and result receipt.
        for action in conn.execute("SELECT * FROM actions WHERE status = 'PASSED'").fetchall():
            modern_verdict = any(
                receipt["kind"] in {"verifier.verdict", "schema.verdict_bound"}
                and payload.get("action_id") == action["id"]
                and payload.get("attempt_key") == action["attempt_key"]
                and payload.get("result_receipt_id") == action["result_receipt_id"]
                and payload.get("verdict") == "pass"
                for receipt, payload in parsed_receipts
            )
            if modern_verdict:
                continue
            sources = [
                (receipt, payload)
                for receipt, payload in parsed_receipts
                if receipt["project_id"] == action["project_id"]
                and receipt["kind"] == "verifier.verdict"
                and payload.get("action_id") == action["id"]
                and payload.get("verdict") == "pass"
            ]
            if not sources or not action["result_receipt_id"] or not action["verifier_id"]:
                continue
            source_receipt, _source_payload = sources[-1]
            self._append_receipt(
                conn,
                action["project_id"],
                "schema.verdict_bound",
                {
                    "action_id": action["id"],
                    "attempt_key": action["attempt_key"],
                    "result_receipt_id": action["result_receipt_id"],
                    "worker_id": action["worker_id"],
                    "verifier_id": action["verifier_id"],
                    "verdict": "pass",
                    "next_status": "PASSED",
                    "source_receipt_id": source_receipt["id"],
                    "schema_version": CLOSURE_SCHEMA_VERSION,
                },
            )

        # Existing request keys can be rebound deterministically from stored contracts.
        for request_row in conn.execute(
            "SELECT request_key, project_id, request_digest FROM project_requests"
        ).fetchall():
            project = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (request_row["project_id"],)
            ).fetchone()
            if project is None:
                continue
            action_rows = conn.execute(
                "SELECT * FROM actions WHERE project_id = ? ORDER BY rowid",
                (request_row["project_id"],),
            ).fetchall()
            stored_spec = {
                "name": project["name"],
                "objective": project["objective"],
                "closed_when": json.loads(project["closed_when_json"]),
                "proposer_id": project["proposer_id"],
                "ratifier_id": project["ratifier_id"],
                "actions": [
                    {
                        "title": action["title"],
                        "success_criteria": json.loads(action["success_criteria_json"]),
                        "value": action["value"],
                        "probability": action["probability"],
                        "remaining_effort": action["remaining_effort"],
                        "max_attempts": action["max_attempts"],
                        "risk": action["risk"],
                        "requires_approval": bool(action["requires_approval"]),
                    }
                    for action in action_rows
                ],
                "max_attempts": project["max_attempts"],
                "max_effects": project["max_effects"],
            }
            digest = request_row["request_digest"] or hashlib.sha256(
                _canonical(stored_spec).encode()
            ).hexdigest()
            if request_row["request_digest"] is None:
                conn.execute(
                    "UPDATE project_requests SET request_digest = ? WHERE request_key = ?",
                    (digest, request_row["request_key"]),
                )
            existing_request_bindings = [
                payload
                for receipt, payload in parsed_receipts
                if receipt["kind"] in {"project.request_bound", "schema.request_bound"}
                and payload.get("request_key") == request_row["request_key"]
            ]
            if not existing_request_bindings:
                self._append_receipt(
                    conn,
                    request_row["project_id"],
                    "schema.request_bound",
                    {
                        "request_key": request_row["request_key"],
                        "project_id": request_row["project_id"],
                        "request_digest": digest,
                        "schema_version": CLOSURE_SCHEMA_VERSION,
                    },
                )

        # Backfill missing reservation receipts with an explicit migration marker.
        for effect in conn.execute(
            "SELECT * FROM effect_reservations WHERE receipt_id IS NULL OR receipt_id = ''"
        ).fetchall():
            action = conn.execute(
                "SELECT * FROM actions WHERE id = ?", (effect["action_id"],)
            ).fetchone()
            if action is None:
                continue
            receipt = self._append_receipt(
                conn,
                action["project_id"],
                "effect.reserved",
                {
                    "action_id": action["id"],
                    "attempt_key": effect["attempt_key"],
                    "idempotency_key": effect["idempotency_key"],
                    "effect_digest": effect["effect_digest"],
                    "approval_ref": effect["approval_ref"],
                    "migrated_legacy_record": True,
                },
            )
            conn.execute(
                "UPDATE effect_reservations SET receipt_id = ? WHERE idempotency_key = ?",
                (receipt["id"], effect["idempotency_key"]),
            )

        current_receipts = {
            row["id"]: (row, json.loads(row["payload_json"]))
            for row in conn.execute("SELECT * FROM receipts ORDER BY seq")
        }
        for effect in conn.execute("SELECT * FROM effect_reservations").fetchall():
            source = current_receipts.get(effect["receipt_id"])
            identity_fields = (
                "idempotency_key",
                "action_id",
                "attempt_key",
                "effect_digest",
                "approval_ref",
            )
            provenance_conflict = False
            provenance_cursor = source
            seen_provenance: set[str] = set()
            while provenance_cursor and provenance_cursor[0]["kind"] == "schema.effect_bound":
                source_receipt_id = provenance_cursor[1].get("source_receipt_id")
                if (
                    not isinstance(source_receipt_id, str)
                    or source_receipt_id in seen_provenance
                    or source_receipt_id not in current_receipts
                ):
                    provenance_conflict = True
                    break
                seen_provenance.add(source_receipt_id)
                provenance_cursor = current_receipts[source_receipt_id]
                if any(
                    field in provenance_cursor[1]
                    and provenance_cursor[1].get(field) != effect[field]
                    for field in identity_fields
                ):
                    provenance_conflict = True
                    break
            is_current_binding = bool(
                source
                and not provenance_conflict
                and source[0]["kind"] in {"effect.reserved", "schema.effect_bound"}
                and all(source[1].get(field) == effect[field] for field in identity_fields)
            )
            if is_current_binding:
                continue
            source_payload = source[1] if source else {}
            present_conflicts = [
                field
                for field in identity_fields
                if field in source_payload and source_payload.get(field) != effect[field]
            ]
            missing_identity = [
                field for field in identity_fields if field not in source_payload
            ]
            provably_legacy_unbound = bool(
                source
                and source[0]["kind"] == "effect.reserved"
                and not present_conflicts
                and (
                    legacy_effect_state
                    or "attempt_key" in missing_identity
                    or "effect_digest" in missing_identity
                )
            )
            if not provably_legacy_unbound:
                current_action = conn.execute(
                    "SELECT * FROM actions WHERE id = ?", (effect["action_id"],)
                ).fetchone()
                affected_project_ids = {source[0]["project_id"]} if source else set()
                if current_action is not None:
                    affected_project_ids.add(current_action["project_id"])
                already_recorded = {
                    row["project_id"]
                    for row, payload in current_receipts.values()
                    if row["kind"] == "effect.integrity_violation"
                    and payload.get("idempotency_key") == effect["idempotency_key"]
                }
                for project_id in affected_project_ids - already_recorded:
                    self._append_receipt(
                        conn,
                        project_id,
                        "effect.integrity_violation",
                        {
                            "idempotency_key": effect["idempotency_key"],
                            "reason": "mutable_effect_identity_disagrees_with_receipt",
                            "conflicting_fields": present_conflicts
                            or missing_identity
                            or (["provenance"] if provenance_conflict else []),
                            "source_receipt_id": effect["receipt_id"],
                        },
                    )
                integrity_digest = hashlib.sha256(
                    f"effect-identity-integrity-violation:{effect['idempotency_key']}".encode()
                ).hexdigest()
                conn.execute(
                    """UPDATE effect_reservations
                       SET status = 'UNKNOWN', result_digest = ?, updated_at = ?
                       WHERE idempotency_key = ?""",
                    (integrity_digest, timestamp, effect["idempotency_key"]),
                )
                affected_action_ids = {effect["action_id"]}
                if source_payload.get("action_id"):
                    affected_action_ids.add(str(source_payload["action_id"]))
                for affected_action_id in affected_action_ids:
                    affected_action = conn.execute(
                        "SELECT * FROM actions WHERE id = ?", (affected_action_id,)
                    ).fetchone()
                    if affected_action is None or affected_action["status"] == "QUARANTINED":
                        continue
                    conn.execute(
                        "UPDATE actions SET status = 'QUARANTINED', updated_at = ? WHERE id = ?",
                        (timestamp, affected_action_id),
                    )
                    self._append_receipt(
                        conn,
                        affected_action["project_id"],
                        "schema.action_contained",
                        {
                            "action_id": affected_action_id,
                            "from": affected_action["status"],
                            "to": "QUARANTINED",
                            "reason": "effect_identity_integrity_violation",
                        },
                    )
                for project_id in affected_project_ids:
                    self._sync_project_status(conn, project_id)
                continue
            action = conn.execute(
                "SELECT project_id FROM actions WHERE id = ?", (effect["action_id"],)
            ).fetchone()
            if action is None:
                continue
            binding = self._append_receipt(
                conn,
                action["project_id"],
                "schema.effect_bound",
                {
                    "action_id": effect["action_id"],
                    "attempt_key": effect["attempt_key"],
                    "idempotency_key": effect["idempotency_key"],
                    "effect_digest": effect["effect_digest"],
                    "approval_ref": effect["approval_ref"],
                    "source_receipt_id": effect["receipt_id"],
                    "schema_version": CLOSURE_SCHEMA_VERSION,
                },
            )
            conn.execute(
                "UPDATE effect_reservations SET receipt_id = ? WHERE idempotency_key = ?",
                (binding["id"], effect["idempotency_key"]),
            )

        if legacy_effect_state:
            contained_projects: set[str] = set()
            for effect in conn.execute("SELECT * FROM effect_reservations").fetchall():
                integrity_violation = conn.execute(
                    """SELECT 1 FROM receipts
                       WHERE kind = 'effect.integrity_violation'
                       AND json_extract(payload_json, '$.idempotency_key') = ? LIMIT 1""",
                    (effect["idempotency_key"],),
                ).fetchone()
                if integrity_violation:
                    continue
                action = conn.execute(
                    "SELECT * FROM actions WHERE id = ?", (effect["action_id"],)
                ).fetchone()
                if action is None:
                    continue
                self._append_receipt(
                    conn,
                    action["project_id"],
                    "effect.migration_quarantined",
                    {
                        "action_id": action["id"],
                        "attempt_key": effect["attempt_key"],
                        "idempotency_key": effect["idempotency_key"],
                        "from": "RESERVED",
                        "to": "UNKNOWN",
                        "reason": "legacy_effect_outcome_unbound",
                        "result_digest": effect["result_digest"],
                    },
                )
                if action["status"] == "PASSED":
                    conn.execute(
                        "UPDATE actions SET status = 'QUARANTINED', updated_at = ? WHERE id = ?",
                        (timestamp, action["id"]),
                    )
                    self._append_receipt(
                        conn,
                        action["project_id"],
                        "schema.action_contained",
                        {
                            "action_id": action["id"],
                            "from": "PASSED",
                            "to": "QUARANTINED",
                            "reason": "legacy_effect_outcome_unbound",
                        },
                    )
                    contained_projects.add(action["project_id"])
            for project_id in contained_projects:
                self._sync_project_status(conn, project_id)

        # Resolve duplicate legacy bindings conservatively before enforcing uniqueness.
        duplicates = conn.execute(
            """SELECT action_id, attempt_key, effect_digest
               FROM effect_reservations
               GROUP BY action_id, attempt_key, effect_digest HAVING COUNT(*) > 1"""
        ).fetchall()
        for duplicate in duplicates:
            rows = conn.execute(
                """SELECT idempotency_key FROM effect_reservations
                   WHERE action_id = ? AND attempt_key = ? AND effect_digest = ?
                   ORDER BY created_at, idempotency_key""",
                (duplicate["action_id"], duplicate["attempt_key"], duplicate["effect_digest"]),
            ).fetchall()
            for row in rows[1:]:
                conn.execute(
                    """UPDATE effect_reservations
                       SET attempt_key = attempt_key || ':legacy-duplicate:' || idempotency_key,
                           status = 'UNKNOWN', updated_at = ?
                       WHERE idempotency_key = ?""",
                    (timestamp, row["idempotency_key"]),
                )

        conn.execute(
            """INSERT INTO closure_schema_meta(singleton, version, migrated_at)
               VALUES (1, ?, ?)
               ON CONFLICT(singleton) DO UPDATE
               SET version = excluded.version, migrated_at = excluded.migrated_at""",
            (CLOSURE_SCHEMA_VERSION, timestamp),
        )

    def _initialize(self) -> None:
        with self._schema_lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    closed_when_json TEXT NOT NULL,
                    proposer_id TEXT NOT NULL,
                    ratifier_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    max_effects INTEGER NOT NULL,
                    spent_attempts INTEGER NOT NULL DEFAULT 0,
                    reserved_effects INTEGER NOT NULL DEFAULT 0,
                    halted_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    success_criteria_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    value REAL NOT NULL,
                    probability REAL NOT NULL,
                    remaining_effort REAL NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    risk TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL,
                    worker_id TEXT,
                    verifier_id TEXT,
                    attempt_key TEXT,
                    result_receipt_id TEXT,
                    last_summary TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_actions_project ON actions(project_id);
                CREATE TABLE IF NOT EXISTS project_requests (
                    request_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    request_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS obstacles (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    action_id TEXT NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
                    obstacle_class TEXT NOT NULL,
                    hard_reason TEXT,
                    description TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    next_step TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_obstacles_project ON obstacles(project_id, active);
                CREATE TABLE IF NOT EXISTS claims (
                    claim_key TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
                    worker_id TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS effect_reservations (
                    idempotency_key TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
                    attempt_key TEXT NOT NULL,
                    effect_digest TEXT NOT NULL,
                    approval_ref TEXT,
                    status TEXT NOT NULL DEFAULT 'RESERVED',
                    adapter_id TEXT,
                    result_digest TEXT,
                    dispatch_token TEXT,
                    receipt_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS effect_adoptions (
                    idempotency_key TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
                    attempt_key TEXT NOT NULL,
                    effect_digest TEXT NOT NULL,
                    source_idempotency_key TEXT NOT NULL
                        REFERENCES effect_reservations(idempotency_key),
                    approval_ref TEXT,
                    receipt_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(action_id, attempt_key, effect_digest)
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    receipt_hash TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_receipts_project ON receipts(project_id, seq);
                CREATE TRIGGER IF NOT EXISTS receipts_no_update
                BEFORE UPDATE ON receipts
                BEGIN
                    SELECT RAISE(ABORT, 'receipts are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS receipts_no_delete
                BEFORE DELETE ON receipts
                BEGIN
                    SELECT RAISE(ABORT, 'receipts are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS projects_contract_no_update
                BEFORE UPDATE OF name, objective, closed_when_json, proposer_id, ratifier_id,
                                 max_attempts, max_effects ON projects
                BEGIN
                    SELECT RAISE(ABORT, 'ratified project contracts are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS actions_contract_no_update
                BEFORE UPDATE OF project_id, title, success_criteria_json, value, probability,
                                 remaining_effort, max_attempts, risk, requires_approval ON actions
                BEGIN
                    SELECT RAISE(ABORT, 'ratified action contracts are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS actions_no_delete
                BEFORE DELETE ON actions
                BEGIN
                    SELECT RAISE(ABORT, 'ratified actions cannot be deleted');
                END;
                CREATE TRIGGER IF NOT EXISTS effects_no_delete
                BEFORE DELETE ON effect_reservations
                BEGIN
                    SELECT RAISE(ABORT, 'effect obligations cannot be deleted');
                END;
                CREATE TRIGGER IF NOT EXISTS claims_no_delete
                BEFORE DELETE ON claims
                BEGIN
                    SELECT RAISE(ABORT, 'attempt claims cannot be deleted');
                END;
                CREATE TRIGGER IF NOT EXISTS effect_adoptions_no_delete
                BEFORE DELETE ON effect_adoptions
                BEGIN
                    SELECT RAISE(ABORT, 'effect adoption evidence cannot be deleted');
                END;
                CREATE TRIGGER IF NOT EXISTS obstacles_contract_no_update
                BEFORE UPDATE OF project_id, action_id, obstacle_class, hard_reason, description,
                                 evidence_json, next_step ON obstacles
                BEGIN
                    SELECT RAISE(ABORT, 'evidence-backed obstacle fields are immutable');
                END;
                """
            )
            # Serialize introspection + ALTER across processes and Store instances.
            # executescript commits implicitly, so the write lock begins after the
            # idempotent base DDL and remains held through version/index updates.
            conn.execute("BEGIN IMMEDIATE")
            self._migrate_schema(conn)
            conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_effect_attempt_digest
                   ON effect_reservations(action_id, attempt_key, effect_digest)"""
            )
            conn.execute(
                """CREATE TRIGGER IF NOT EXISTS project_requests_no_update
                   BEFORE UPDATE ON project_requests
                   BEGIN
                       SELECT RAISE(ABORT, 'project request bindings are immutable');
                   END"""
            )
            conn.execute(
                """CREATE TRIGGER IF NOT EXISTS project_requests_no_delete
                   BEFORE DELETE ON project_requests
                   BEGIN
                       SELECT RAISE(ABORT, 'project request bindings cannot be deleted');
                   END"""
            )
            conn.execute(
                """CREATE TRIGGER IF NOT EXISTS effects_identity_no_update
                   BEFORE UPDATE OF action_id, attempt_key, effect_digest, approval_ref,
                                    receipt_id ON effect_reservations
                   BEGIN
                       SELECT RAISE(ABORT, 'effect reservation identity is immutable');
                   END"""
            )
            conn.execute(
                """CREATE TRIGGER IF NOT EXISTS effect_adoptions_no_update
                   BEFORE UPDATE ON effect_adoptions
                   BEGIN
                       SELECT RAISE(ABORT, 'effect adoption evidence is immutable');
                   END"""
            )

    @staticmethod
    def _row(conn: sqlite3.Connection, table: str, row_id: str) -> sqlite3.Row:
        if table not in {"projects", "actions"}:
            raise ValueError("unsupported table")
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            noun = "Project" if table == "projects" else "Action"
            raise ClosureError("not_found", f"{noun} {row_id} does not exist", 404)
        return row

    def _append_receipt(
        self, conn: sqlite3.Connection, project_id: str, kind: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        previous = conn.execute(
            "SELECT receipt_hash FROM receipts ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = previous["receipt_hash"] if previous else "GENESIS"
        created_at = _now()
        safe_payload = _sanitize(payload)
        envelope = {
            "project_id": project_id,
            "kind": kind,
            "payload": safe_payload,
            "prev_hash": prev_hash,
            "created_at": created_at,
        }
        receipt_hash = hashlib.sha256(_canonical(envelope).encode()).hexdigest()
        receipt_id = f"rpt_{receipt_hash[:24]}"
        conn.execute(
            """INSERT INTO receipts
               (id, project_id, kind, payload_json, prev_hash, receipt_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                receipt_id,
                project_id,
                kind,
                _canonical(safe_payload),
                prev_hash,
                receipt_hash,
                created_at,
            ),
        )
        return {
            "id": receipt_id,
            "kind": kind,
            "hash": receipt_hash,
            "prev_hash": prev_hash,
            "created_at": created_at,
        }

    @staticmethod
    def _receipt_view(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "hash": row["receipt_hash"],
            "prev_hash": row["prev_hash"],
            "created_at": row["created_at"],
        }

    def create_project(self, spec: ProjectCreate | dict[str, Any]) -> dict[str, Any]:
        if not isinstance(spec, ProjectCreate):
            spec = ProjectCreate.model_validate(spec)
        project_id = f"prj_{uuid.uuid4().hex[:20]}"
        created_at = _now()
        request_digest = hashlib.sha256(
            _canonical(spec.model_dump(exclude={"request_key"})).encode()
        ).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if spec.request_key:
                existing = conn.execute(
                    "SELECT project_id, request_digest FROM project_requests WHERE request_key = ?",
                    (spec.request_key,),
                ).fetchone()
                receipt_bindings: set[tuple[str, str]] = set()
                for receipt_row in conn.execute(
                    """SELECT payload_json FROM receipts
                       WHERE kind IN ('project.request_bound', 'schema.request_bound')
                       ORDER BY seq"""
                ).fetchall():
                    try:
                        payload = json.loads(receipt_row["payload_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if payload.get("request_key") == spec.request_key:
                        bound_project = payload.get("project_id")
                        bound_digest = payload.get("request_digest")
                        if isinstance(bound_project, str) and isinstance(bound_digest, str):
                            receipt_bindings.add((bound_project, bound_digest))
                if len(receipt_bindings) > 1:
                    raise ClosureError(
                        "request_binding_conflict",
                        "request_key has conflicting immutable receipt bindings",
                        409,
                    )
                if receipt_bindings:
                    bound_project, bound_digest = next(iter(receipt_bindings))
                    if bound_digest != request_digest:
                        raise ClosureError(
                            "idempotency_conflict",
                            "request_key is already bound to a different project contract",
                            409,
                        )
                    if existing and (
                        existing["project_id"] != bound_project
                        or existing["request_digest"] != bound_digest
                    ):
                        raise ClosureError(
                            "request_projection_mismatch",
                            "Mutable request mapping conflicts with its immutable receipt",
                            409,
                        )
                    if not existing:
                        conn.execute(
                            """INSERT INTO project_requests
                               (request_key, project_id, request_digest, created_at)
                               VALUES (?, ?, ?, ?)""",
                            (spec.request_key, bound_project, bound_digest, created_at),
                        )
                        self._append_receipt(
                            conn,
                            bound_project,
                            "project.request_projection_repaired",
                            {"request_key": spec.request_key, "project_id": bound_project},
                        )
                    return self._project_view(conn, bound_project)
                if existing:
                    if existing["request_digest"] != request_digest:
                        raise ClosureError(
                            "idempotency_conflict",
                            "request_key is already bound to a different project contract",
                            409,
                        )
                    return self._project_view(conn, existing["project_id"])
            conn.execute(
                """INSERT INTO projects
                   (id, name, objective, closed_when_json, proposer_id, ratifier_id,
                    status, max_attempts, max_effects, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'READY', ?, ?, ?, ?)""",
                (
                    project_id,
                    _sanitize(spec.name),
                    _sanitize(spec.objective),
                    _canonical(_sanitize(spec.closed_when)),
                    _sanitize(spec.proposer_id),
                    _sanitize(spec.ratifier_id),
                    spec.max_attempts,
                    spec.max_effects,
                    created_at,
                    created_at,
                ),
            )
            action_ids: list[str] = []
            ratified_actions: list[dict[str, Any]] = []
            for action in spec.actions:
                action_id = f"act_{uuid.uuid4().hex[:20]}"
                action_ids.append(action_id)
                ratified_actions.append(
                    {
                        "id": action_id,
                        "title": action.title,
                        "success_criteria": action.success_criteria,
                        "value": action.value,
                        "probability": action.probability,
                        "remaining_effort": action.remaining_effort,
                        "max_attempts": action.max_attempts,
                        "risk": action.risk,
                        "requires_approval": action.requires_approval,
                    }
                )
                conn.execute(
                    """INSERT INTO actions
                       (id, project_id, title, success_criteria_json, status, value,
                        probability, remaining_effort, max_attempts, risk,
                        requires_approval, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'READY', ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        action_id,
                        project_id,
                        _sanitize(action.title),
                        _canonical(_sanitize(action.success_criteria)),
                        action.value,
                        action.probability,
                        action.remaining_effort,
                        action.max_attempts,
                        action.risk,
                        int(action.requires_approval),
                        created_at,
                        created_at,
                    ),
                )
            self._append_receipt(
                conn,
                project_id,
                "contract.ratified",
                {
                    "name": spec.name,
                    "objective": spec.objective,
                    "closed_when": spec.closed_when,
                    "proposer_id": spec.proposer_id,
                    "ratifier_id": spec.ratifier_id,
                    "action_ids": action_ids,
                    "actions": ratified_actions,
                    "budgets": {
                        "max_attempts": spec.max_attempts,
                        "max_effects": spec.max_effects,
                    },
                },
            )
            if spec.request_key:
                conn.execute(
                    """INSERT INTO project_requests
                       (request_key, project_id, request_digest, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (spec.request_key, project_id, request_digest, created_at),
                )
                self._append_receipt(
                    conn,
                    project_id,
                    "project.request_bound",
                    {
                        "request_key": spec.request_key,
                        "project_id": project_id,
                        "request_digest": request_digest,
                    },
                )
        return self.get_project(project_id)

    @staticmethod
    def _leverage(row: sqlite3.Row) -> float:
        return round(float(row["value"]) * float(row["probability"]) / float(row["remaining_effort"]), 6)

    @staticmethod
    def _derived_project_status(project: sqlite3.Row, states: list[str]) -> str:
        if project["halted_reason"]:
            return "QUARANTINED"
        if states and all(state == "PASSED" for state in states):
            return "PASSED"
        if "VERIFYING" in states:
            return "VERIFYING"
        if "RUNNING" in states:
            return "RUNNING"
        if any(state in RUNNABLE_STATES for state in states):
            return "RUNNING" if int(project["spent_attempts"]) else "READY"
        incomplete = [state for state in states if state != "PASSED"]
        if incomplete and all(state == "QUARANTINED" for state in incomplete):
            return "QUARANTINED"
        if incomplete and all(state == "APPROVAL_PENDING" for state in incomplete):
            return "APPROVAL_PENDING"
        if incomplete:
            return "LOCAL_BLOCKED"
        return "READY"

    @staticmethod
    def _ratified_action_ids(conn: sqlite3.Connection, project_id: str) -> set[str] | None:
        rows = conn.execute(
            """SELECT payload_json FROM receipts
               WHERE project_id = ? AND kind IN ('contract.ratified', 'schema.contract_bound')
               ORDER BY seq DESC""",
            (project_id,),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            action_ids = payload.get("action_ids")
            if isinstance(action_ids, list) and action_ids:
                return {str(action_id) for action_id in action_ids}
        return None

    def _sync_project_status(self, conn: sqlite3.Connection, project_id: str) -> str:
        project = self._row(conn, "projects", project_id)
        actions = conn.execute(
            "SELECT id, status FROM actions WHERE project_id = ?", (project_id,)
        ).fetchall()
        ratified_ids = self._ratified_action_ids(conn, project_id)
        actual_ids = {row["id"] for row in actions}
        membership_ok = ratified_ids is not None and ratified_ids == actual_ids
        contract_integrity_violation = conn.execute(
            """SELECT 1 FROM receipts
               WHERE project_id = ? AND kind IN (
                   'contract.integrity_violation', 'effect.integrity_violation'
               ) LIMIT 1""",
            (project_id,),
        ).fetchone()
        status = (
            self._derived_project_status(project, [row["status"] for row in actions])
            if membership_ok and contract_integrity_violation is None
            else "QUARANTINED"
        )
        if status != project["status"]:
            if not membership_ok:
                self._append_receipt(
                    conn,
                    project_id,
                    "contract.membership_violation",
                    {
                        "missing_action_ids": sorted((ratified_ids or set()) - actual_ids),
                        "unexpected_action_ids": sorted(actual_ids - (ratified_ids or set())),
                    },
                )
            conn.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), project_id),
            )
            self._append_receipt(
                conn,
                project_id,
                "project.state_changed",
                {"from": project["status"], "to": status},
            )
        return status

    def _contain_exhausted_attempt_paths(
        self, conn: sqlite3.Connection, project_id: str
    ) -> list[str]:
        """Quarantine only not-yet-started paths when the project attempt cap is spent."""
        project = self._row(conn, "projects", project_id)
        if int(project["spent_attempts"]) < int(project["max_attempts"]):
            return []
        rows = conn.execute(
            """SELECT id FROM actions WHERE project_id = ?
               AND status IN ('READY', 'FAILED_RETRYABLE', 'LOCAL_BLOCKED', 'APPROVAL_PENDING')""",
            (project_id,),
        ).fetchall()
        action_ids = [row["id"] for row in rows]
        if not action_ids:
            return []
        timestamp = _now()
        placeholders = ",".join("?" for _ in action_ids)
        conn.execute(
            f"UPDATE actions SET status = 'QUARANTINED', updated_at = ? WHERE id IN ({placeholders})",
            (timestamp, *action_ids),
        )
        self._append_receipt(
            conn,
            project_id,
            "budget.paths_contained",
            {"budget": "project_attempts", "action_ids": action_ids},
        )
        return action_ids

    def _project_view(self, conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        project = self._row(conn, "projects", project_id)
        action_rows = conn.execute(
            "SELECT * FROM actions WHERE project_id = ?", (project_id,)
        ).fetchall()
        actions: list[dict[str, Any]] = []
        for row in action_rows:
            actions.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "success_criteria": json.loads(row["success_criteria_json"]),
                    "status": row["status"],
                    "closure_leverage": self._leverage(row),
                    "value": row["value"],
                    "probability": row["probability"],
                    "remaining_effort": row["remaining_effort"],
                    "attempt_count": row["attempt_count"],
                    "max_attempts": row["max_attempts"],
                    "risk": row["risk"],
                    "requires_approval": bool(row["requires_approval"]),
                    "worker_id": row["worker_id"],
                    "verifier_id": row["verifier_id"],
                    "last_summary": row["last_summary"],
                }
            )
        actions.sort(key=lambda item: (-item["closure_leverage"], item["title"]))
        obstacle_rows = conn.execute(
            "SELECT * FROM obstacles WHERE project_id = ? AND active = 1 ORDER BY created_at",
            (project_id,),
        ).fetchall()
        obstacles = [
            {
                "id": row["id"],
                "action_id": row["action_id"],
                "class": row["obstacle_class"],
                "hard_reason": row["hard_reason"],
                "description": row["description"],
                "evidence": json.loads(row["evidence_json"]),
                "next_step": row["next_step"],
            }
            for row in obstacle_rows
        ]
        runnable = [action for action in actions if action["status"] in RUNNABLE_STATES]
        active = [action for action in actions if action["status"] in {"RUNNING", "VERIFYING"}]
        incomplete = [action for action in actions if action["status"] not in TERMINAL_STATES]
        true_obstacles = [
            item for item in obstacles if item["class"] in {"HARD BLOCKER", "APPROVAL GATE"}
        ]
        apparent_obstacles = [item for item in obstacles if item not in true_obstacles]
        no_runnable_path = bool(incomplete) and not runnable and not active
        true_blocked_actions = {item["action_id"] for item in true_obstacles}
        every_open_path_is_hard = bool(incomplete) and all(
            item["id"] in true_blocked_actions for item in incomplete
        )
        if no_runnable_path and every_open_path_is_hard:
            blocker_truth = "true"
        elif no_runnable_path and obstacles:
            blocker_truth = "apparent"
        else:
            blocker_truth = "none"
        receipt_count = conn.execute(
            "SELECT COUNT(*) AS n FROM receipts WHERE project_id = ?", (project_id,)
        ).fetchone()["n"]
        next_action = runnable[0] if runnable else None
        return {
            "id": project["id"],
            "name": project["name"],
            "objective": project["objective"],
            "status": project["status"],
            "contract": {
                "closed_when": json.loads(project["closed_when_json"]),
                "proposer_id": project["proposer_id"],
                "ratifier_id": project["ratifier_id"],
            },
            "closure": {
                "closed_when": json.loads(project["closed_when_json"]),
                "now": [item["title"] for item in active],
                "next": next_action["title"] if next_action else None,
                "final": "Independent verification of every closure action",
            },
            "closure_leverage": next_action["closure_leverage"] if next_action else 0.0,
            "blocker_truth": blocker_truth,
            "is_blocked": blocker_truth == "true",
            "true_blockers": true_obstacles,
            "apparent_blockers": apparent_obstacles,
            "budget": {
                "attempts": project["spent_attempts"],
                "max_attempts": project["max_attempts"],
                "effects": project["reserved_effects"],
                "max_effects": project["max_effects"],
            },
            "actions": actions,
            "receipt_count": receipt_count,
            "halted_reason": project["halted_reason"],
            "created_at": project["created_at"],
            "updated_at": project["updated_at"],
        }

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            return self._project_view(conn, project_id)

    def action_authority_context(self, action_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            action = self._row(conn, "actions", action_id)
        return {
            "action_id": action["id"],
            "worker_id": action["worker_id"],
            "attempt_key": action["attempt_key"],
            "status": action["status"],
        }

    def board(self) -> dict[str, Any]:
        with self._connect() as conn:
            ids = [row["id"] for row in conn.execute("SELECT id FROM projects").fetchall()]
            projects = [self._project_view(conn, project_id) for project_id in ids]
        projects.sort(
            key=lambda item: (
                item["status"] == "PASSED",
                -item["closure_leverage"],
                item["name"].lower(),
            )
        )
        return {
            "projects": projects,
            "metrics": {
                "total": len(projects),
                "closed": sum(item["status"] == "PASSED" for item in projects),
                "true_blockers": sum(item["blocker_truth"] == "true" for item in projects),
                "apparent_blockers": sum(item["blocker_truth"] == "apparent" for item in projects),
                "active": sum(item["status"] in {"RUNNING", "VERIFYING"} for item in projects),
            },
            "ranking": "value * probability_of_finishing_now / remaining_effort",
            "conformance": "UNSCORED",
        }

    def claim_action(self, action_id: str, request: ClaimRequest | dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, ClaimRequest):
            request = ClaimRequest.model_validate(request)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM claims WHERE claim_key = ?", (request.claim_key,)
            ).fetchone()
            if existing:
                if existing["action_id"] != action_id or existing["worker_id"] != request.worker_id:
                    raise ClosureError(
                        "idempotency_conflict",
                        "claim_key is already bound to a different claim",
                        409,
                    )
                action = self._row(conn, "actions", action_id)
                if action["attempt_key"] != request.claim_key:
                    raise ClosureError(
                        "stale_attempt",
                        "A historical claim_key cannot resume or observe the current attempt",
                        409,
                    )
                if action["status"] in {"LOCAL_BLOCKED", "APPROVAL_PENDING"}:
                    if not request.resolution_evidence:
                        raise ClosureError(
                            "resolution_evidence_required",
                            "Suspended work may resume only with resolution evidence",
                        )
                    if action["status"] == "APPROVAL_PENDING" and not request.approval_ref:
                        raise ClosureError(
                            "approval_required", "An approval reference is required", 403
                        )
                    timestamp = _now()
                    conn.execute(
                        "UPDATE actions SET status = 'RUNNING', updated_at = ? WHERE id = ?",
                        (timestamp, action_id),
                    )
                    conn.execute(
                        "UPDATE obstacles SET active = 0 WHERE action_id = ? AND active = 1",
                        (action_id,),
                    )
                    receipt = self._append_receipt(
                        conn,
                        action["project_id"],
                        "action.resumed",
                        {
                            "action_id": action_id,
                            "attempt_key": request.claim_key,
                            "worker_id": request.worker_id,
                            "approval_ref": request.approval_ref,
                            "resolution_evidence": [
                                item.model_dump() for item in request.resolution_evidence
                            ],
                        },
                    )
                    self._sync_project_status(conn, action["project_id"])
                    return {
                        "idempotent": False,
                        "resumed": True,
                        "attempt_key": request.claim_key,
                        "receipt": receipt,
                        "project": self._project_view(conn, action["project_id"]),
                    }
                return {
                    "idempotent": True,
                    "attempt_key": action["attempt_key"],
                    "receipt_id": existing["receipt_id"],
                    "project": self._project_view(conn, action["project_id"]),
                }
            action = self._row(conn, "actions", action_id)
            project = self._row(conn, "projects", action["project_id"])
            if project["halted_reason"]:
                raise ClosureError("operator_halt", "Project is halted", 409)
            if project["status"] == "QUARANTINED" and action["status"] != "PASSED":
                raise ClosureError(
                    "project_quarantined",
                    "A QUARANTINED project cannot start or resume work",
                    409,
                )
            if action["status"] == "RUNNING":
                raise ClosureError(
                    "attempt_in_progress",
                    "Resume a running action with its original claim_key",
                    409,
                )
            if action["status"] not in RUNNABLE_STATES | {"LOCAL_BLOCKED", "APPROVAL_PENDING"}:
                raise ClosureError(
                    "illegal_transition",
                    f"Cannot claim an action in {action['status']}",
                    409,
                )
            if action["status"] in {"LOCAL_BLOCKED", "APPROVAL_PENDING"}:
                raise ClosureError(
                    "attempt_key_mismatch",
                    "Suspended work must resume with its original claim_key",
                    409,
                )
            action_budget_exhausted = int(action["attempt_count"]) >= int(action["max_attempts"])
            project_budget_exhausted = int(project["spent_attempts"]) >= int(project["max_attempts"])
            if action_budget_exhausted or project_budget_exhausted:
                timestamp = _now()
                if project_budget_exhausted:
                    self._contain_exhausted_attempt_paths(conn, action["project_id"])
                else:
                    conn.execute(
                        "UPDATE actions SET status = 'QUARANTINED', updated_at = ? WHERE id = ?",
                        (timestamp, action_id),
                    )
                self._append_receipt(
                    conn,
                    action["project_id"],
                    "budget.exhausted",
                    {
                        "action_id": action_id,
                        "budget": "project_attempts" if project_budget_exhausted else "action_attempts",
                    },
                )
                self._sync_project_status(conn, action["project_id"])
                # Persist containment before surfacing the denied transition.
                conn.commit()
                raise ClosureError("attempt_budget_exhausted", "Attempt budget is exhausted", 409)
            unattempted_others = conn.execute(
                """SELECT COUNT(*) FROM actions
                   WHERE project_id = ? AND id != ? AND attempt_count = 0
                   AND status != 'PASSED'""",
                (action["project_id"], action_id),
            ).fetchone()[0]
            remaining_after_claim = int(project["max_attempts"]) - (
                int(project["spent_attempts"]) + 1
            )
            if remaining_after_claim < int(unattempted_others):
                raise ClosureError(
                    "required_attempts_reserved",
                    "Retry denied because the remaining project budget is reserved for "
                    "required actions that have not received an attempt",
                    409,
                )
            timestamp = _now()
            conn.execute(
                """UPDATE actions SET status = 'RUNNING', worker_id = ?, verifier_id = NULL,
                   attempt_key = ?, result_receipt_id = NULL,
                   attempt_count = attempt_count + 1, updated_at = ? WHERE id = ?""",
                (request.worker_id, request.claim_key, timestamp, action_id),
            )
            conn.execute(
                "UPDATE projects SET spent_attempts = spent_attempts + 1, updated_at = ? WHERE id = ?",
                (timestamp, action["project_id"]),
            )
            conn.execute(
                "UPDATE obstacles SET active = 0 WHERE action_id = ? AND active = 1", (action_id,)
            )
            receipt = self._append_receipt(
                conn,
                action["project_id"],
                "action.claimed",
                {
                    "action_id": action_id,
                    "attempt_key": request.claim_key,
                    "worker_id": request.worker_id,
                    "approval_ref": request.approval_ref,
                    "resolution_evidence": [item.model_dump() for item in request.resolution_evidence],
                },
            )
            conn.execute(
                "INSERT INTO claims VALUES (?, ?, ?, ?, ?)",
                (request.claim_key, action_id, request.worker_id, receipt["id"], timestamp),
            )
            self._contain_exhausted_attempt_paths(conn, action["project_id"])
            self._sync_project_status(conn, action["project_id"])
            project_view = self._project_view(conn, action["project_id"])
        return {
            "idempotent": False,
            "resumed": False,
            "attempt_key": request.claim_key,
            "receipt": receipt,
            "project": project_view,
        }

    def _fail_undispatched_effects(
        self,
        conn: sqlite3.Connection,
        action: sqlite3.Row,
        attempt_key: str,
        reason: str,
    ) -> list[str]:
        reserved = conn.execute(
            """SELECT idempotency_key FROM effect_reservations
               WHERE action_id = ? AND attempt_key = ? AND status = 'RESERVED'""",
            (action["id"], attempt_key),
        ).fetchall()
        if not reserved:
            return []
        result_digest = hashlib.sha256(
            f"attempt-ended:{attempt_key}:{reason}".encode()
        ).hexdigest()
        conn.execute(
            """UPDATE effect_reservations
               SET status = 'FAILED', result_digest = ?, updated_at = ?
               WHERE action_id = ? AND attempt_key = ? AND status = 'RESERVED'""",
            (result_digest, _now(), action["id"], attempt_key),
        )
        keys = [row["idempotency_key"] for row in reserved]
        self._append_receipt(
            conn,
            action["project_id"],
            "effect.not_dispatched",
            {
                "action_id": action["id"],
                "attempt_key": attempt_key,
                "idempotency_keys": keys,
                "result_digest": result_digest,
                "reason": reason,
            },
        )
        return keys

    def record_result(self, action_id: str, request: ResultRequest | dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, ResultRequest):
            request = ResultRequest.model_validate(request)
        if request.outcome == "blocked" and request.obstacle_class is None:
            raise ClosureError("obstacle_class_required", "Blocked results require obstacle_class")
        if request.outcome != "blocked" and request.obstacle_class is not None:
            raise ClosureError("unexpected_obstacle", "Only blocked results may classify an obstacle")
        if request.obstacle_class == "NO BLOCKER":
            raise ClosureError("false_blocker", "NO BLOCKER cannot stop an action")
        if request.obstacle_class == "APPROVAL GATE":
            raise ClosureError(
                "policy_managed_obstacle",
                "APPROVAL GATE is emitted only by the effect approval policy",
            )
        if request.obstacle_class == "HARD BLOCKER" and request.hard_reason not in HARD_REASONS:
            raise ClosureError(
                "unsupported_hard_blocker",
                f"Hard blockers require one of {sorted(HARD_REASONS)}",
            )
        safe_evidence = _sanitize([item.model_dump() for item in request.evidence])
        safe_request = _sanitize(request.model_dump())
        request_digest = hashlib.sha256(_canonical(safe_request).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            action = self._row(conn, "actions", action_id)
            if action["attempt_key"] != request.attempt_key:
                raise ClosureError(
                    "stale_attempt",
                    "Result does not belong to the action's current attempt",
                    409,
                )
            if action["worker_id"] != request.worker_id:
                raise ClosureError("worker_mismatch", "Only the claiming worker may report a result", 403)
            if action["status"] != "RUNNING":
                prior_rows = conn.execute(
                    """SELECT * FROM receipts
                       WHERE project_id = ? AND kind = 'worker.result' ORDER BY seq DESC""",
                    (action["project_id"],),
                ).fetchall()
                for prior in prior_rows:
                    try:
                        payload = json.loads(prior["payload_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if (
                        payload.get("action_id") != action_id
                        or payload.get("attempt_key") != request.attempt_key
                    ):
                        continue
                    legacy_match = all(
                        payload.get(field) == safe_request.get(field)
                        for field in (
                            "worker_id",
                            "outcome",
                            "summary",
                            "evidence",
                            "obstacle_class",
                            "hard_reason",
                            "next_step",
                        )
                    )
                    if payload.get("request_digest") == request_digest or (
                        payload.get("request_digest") is None and legacy_match
                    ):
                        return {
                            "idempotent": True,
                            "receipt": self._receipt_view(prior),
                            "project": self._project_view(conn, action["project_id"]),
                        }
                    raise ClosureError(
                        "idempotency_conflict",
                        "This attempt already has a different worker result",
                        409,
                    )
                raise ClosureError(
                    "illegal_transition", f"Result requires RUNNING, got {action['status']}", 409
                )
            if request.outcome == "success":
                next_status = "VERIFYING"
            elif request.outcome == "failed":
                next_status = (
                    "QUARANTINED"
                    if int(action["attempt_count"]) >= int(action["max_attempts"])
                    else "FAILED_RETRYABLE"
                )
            elif request.obstacle_class in {"HARD BLOCKER", "APPROVAL GATE"}:
                next_status = "APPROVAL_PENDING"
            elif request.obstacle_class == "LOCAL BLOCKER":
                next_status = "LOCAL_BLOCKED"
            else:
                next_status = (
                    "QUARANTINED"
                    if int(action["attempt_count"]) >= int(action["max_attempts"])
                    else "FAILED_RETRYABLE"
                )
            conn.execute(
                "UPDATE actions SET status = ?, last_summary = ?, updated_at = ? WHERE id = ?",
                (next_status, _sanitize(request.summary), _now(), action_id),
            )
            obstacle_id = None
            if request.outcome == "blocked":
                obstacle_id = f"obs_{uuid.uuid4().hex[:20]}"
                conn.execute(
                    """INSERT INTO obstacles
                       (id, project_id, action_id, obstacle_class, hard_reason,
                        description, evidence_json, next_step, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        obstacle_id,
                        action["project_id"],
                        action_id,
                        request.obstacle_class,
                        request.hard_reason,
                        _sanitize(request.summary),
                        _canonical(safe_evidence),
                        _sanitize(request.next_step),
                        _now(),
                    ),
                )
            receipt = self._append_receipt(
                conn,
                action["project_id"],
                "worker.result",
                {
                    "action_id": action_id,
                    "attempt_key": request.attempt_key,
                    "worker_id": request.worker_id,
                    "outcome": request.outcome,
                    "next_status": next_status,
                    "summary": request.summary,
                    "evidence": safe_evidence,
                    "obstacle_class": request.obstacle_class,
                    "hard_reason": request.hard_reason,
                    "obstacle_id": obstacle_id,
                    "next_step": request.next_step,
                    "request_digest": request_digest,
                },
            )
            conn.execute(
                "UPDATE actions SET result_receipt_id = ? WHERE id = ?",
                (receipt["id"], action_id),
            )
            if request.outcome != "success":
                self._fail_undispatched_effects(
                    conn, action, request.attempt_key, f"worker_{request.outcome}"
                )
            self._contain_exhausted_attempt_paths(conn, action["project_id"])
            self._sync_project_status(conn, action["project_id"])
            project_view = self._project_view(conn, action["project_id"])
        return {"idempotent": False, "receipt": receipt, "project": project_view}

    def verify_action(self, action_id: str, request: VerifyRequest | dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, VerifyRequest):
            request = VerifyRequest.model_validate(request)
        safe_request = _sanitize(request.model_dump())
        request_digest = hashlib.sha256(_canonical(safe_request).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            action = self._row(conn, "actions", action_id)
            if action["attempt_key"] != request.attempt_key:
                raise ClosureError(
                    "stale_attempt",
                    "Verdict does not belong to the action's current attempt",
                    409,
                )
            if action["result_receipt_id"] != request.result_receipt_id:
                raise ClosureError(
                    "result_receipt_mismatch",
                    "Verdict must bind to the current worker-result receipt",
                    409,
                )
            if action["worker_id"] == request.verifier_id:
                raise ClosureError(
                    "independence_required", "The worker cannot verify its own result", 403
                )
            if action["status"] != "VERIFYING":
                prior_rows = conn.execute(
                    """SELECT * FROM receipts
                       WHERE project_id = ? AND kind = 'verifier.verdict' ORDER BY seq DESC""",
                    (action["project_id"],),
                ).fetchall()
                for prior in prior_rows:
                    try:
                        payload = json.loads(prior["payload_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if (
                        payload.get("action_id") != action_id
                        or payload.get("attempt_key") != request.attempt_key
                        or payload.get("result_receipt_id") != request.result_receipt_id
                    ):
                        continue
                    legacy_match = all(
                        payload.get(field) == safe_request.get(field)
                        for field in ("verifier_id", "verdict", "summary", "evidence")
                    )
                    if payload.get("request_digest") == request_digest or (
                        payload.get("request_digest") is None and legacy_match
                    ):
                        return {
                            "idempotent": True,
                            "receipt": self._receipt_view(prior),
                            "project": self._project_view(conn, action["project_id"]),
                        }
                    raise ClosureError(
                        "idempotency_conflict",
                        "This result already has a different verifier verdict",
                        409,
                    )
                raise ClosureError(
                    "illegal_transition",
                    f"Verification requires VERIFYING, got {action['status']}",
                    409,
                )
            if request.verdict == "pass":
                receipt_obligations: dict[str, tuple[str | None, str | None]] = {}
                for receipt_row in conn.execute(
                    """SELECT payload_json FROM receipts
                       WHERE project_id = ? AND kind = 'effect.reserved' ORDER BY seq""",
                    (action["project_id"],),
                ).fetchall():
                    try:
                        payload = json.loads(receipt_row["payload_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if (
                        payload.get("action_id") == action_id
                        and payload.get("attempt_key") == request.attempt_key
                        and payload.get("idempotency_key")
                    ):
                        receipt_obligations[payload["idempotency_key"]] = (
                            payload.get("effect_digest"),
                            payload.get("approval_ref"),
                        )
                reservation_rows = {
                    row["idempotency_key"]: row
                    for row in conn.execute(
                        """SELECT * FROM effect_reservations
                           WHERE action_id = ? AND attempt_key = ?""",
                        (action_id, request.attempt_key),
                    ).fetchall()
                }
                missing_obligations = [
                    key
                    for key, (digest, approval_ref) in receipt_obligations.items()
                    if key not in reservation_rows
                    or reservation_rows[key]["effect_digest"] != digest
                    or reservation_rows[key]["approval_ref"] != approval_ref
                ]
                for key, reservation in reservation_rows.items():
                    binding_row = conn.execute(
                        "SELECT id, kind, payload_json FROM receipts WHERE id = ?",
                        (reservation["receipt_id"],),
                    ).fetchone()
                    try:
                        binding_payload = (
                            json.loads(binding_row["payload_json"]) if binding_row else {}
                        )
                    except (TypeError, json.JSONDecodeError):
                        binding_payload = {}
                    binding_invalid = (
                        binding_row is None
                        or binding_row["kind"] not in {"effect.reserved", "schema.effect_bound"}
                        or binding_payload.get("idempotency_key") != key
                        or binding_payload.get("action_id") != reservation["action_id"]
                        or binding_payload.get("attempt_key") != reservation["attempt_key"]
                        or binding_payload.get("effect_digest") != reservation["effect_digest"]
                        or binding_payload.get("approval_ref") != reservation["approval_ref"]
                    )
                    provenance_row = binding_row
                    provenance_payload = binding_payload
                    seen_provenance: set[str] = set()
                    while (
                        not binding_invalid
                        and provenance_row is not None
                        and provenance_row["kind"] == "schema.effect_bound"
                    ):
                        source_receipt_id = provenance_payload.get("source_receipt_id")
                        if (
                            not isinstance(source_receipt_id, str)
                            or source_receipt_id in seen_provenance
                        ):
                            binding_invalid = True
                            break
                        seen_provenance.add(source_receipt_id)
                        provenance_row = conn.execute(
                            "SELECT id, kind, payload_json FROM receipts WHERE id = ?",
                            (source_receipt_id,),
                        ).fetchone()
                        if provenance_row is None:
                            binding_invalid = True
                            break
                        try:
                            provenance_payload = json.loads(provenance_row["payload_json"])
                        except (TypeError, json.JSONDecodeError):
                            binding_invalid = True
                            break
                        if any(
                            field in provenance_payload
                            and provenance_payload.get(field) != reservation[field]
                            for field in (
                                "idempotency_key",
                                "action_id",
                                "attempt_key",
                                "effect_digest",
                                "approval_ref",
                            )
                        ):
                            binding_invalid = True
                    if binding_invalid:
                        missing_obligations.append(key)
                missing_obligations = sorted(set(missing_obligations))
                if missing_obligations:
                    raise ClosureError(
                        "effect_record_missing",
                        "Passing verification requires every receipt-backed effect record; "
                        f"missing {', '.join(missing_obligations)}",
                        409,
                    )
                if bool(action["requires_approval"]):
                    approved_effect = conn.execute(
                        """SELECT 1 FROM effect_reservations
                           WHERE action_id = ? AND attempt_key = ?
                           AND status = 'COMPLETED'
                           AND approval_ref IS NOT NULL AND approval_ref != ''
                           UNION ALL
                           SELECT 1 FROM effect_adoptions adopted
                           JOIN effect_reservations source
                             ON source.idempotency_key = adopted.source_idempotency_key
                           WHERE adopted.action_id = ? AND adopted.attempt_key = ?
                           AND adopted.approval_ref IS NOT NULL AND adopted.approval_ref != ''
                           AND source.status = 'COMPLETED'
                           AND source.approval_ref IS NOT NULL AND source.approval_ref != ''
                           AND source.action_id = adopted.action_id
                           AND source.effect_digest = adopted.effect_digest
                           LIMIT 1""",
                        (action_id, request.attempt_key, action_id, request.attempt_key),
                    ).fetchone()
                    if approved_effect is None:
                        raise ClosureError(
                            "approved_effect_required",
                            "Passing an approval-required action needs a completed, "
                            "approval-bearing effect on the current attempt",
                            409,
                        )
                unresolved = conn.execute(
                    """SELECT idempotency_key, status FROM effect_reservations
                       WHERE action_id = ? AND (
                         (attempt_key = ? AND status != 'COMPLETED')
                         OR status IN ('DISPATCHED', 'UNKNOWN')
                       )""",
                    (action_id, request.attempt_key),
                ).fetchall()
                if unresolved:
                    states = sorted({row["status"] for row in unresolved})
                    raise ClosureError(
                        "effect_reconciliation_required",
                        "Passing verification requires every reserved effect to be COMPLETED; "
                        f"found {', '.join(states)}",
                        409,
                    )
                next_status = "PASSED"
            else:
                next_status = (
                    "QUARANTINED"
                    if int(action["attempt_count"]) >= int(action["max_attempts"])
                    else "FAILED_RETRYABLE"
                )
            conn.execute(
                """UPDATE actions SET status = ?, verifier_id = ?, last_summary = ?,
                   updated_at = ? WHERE id = ?""",
                (
                    next_status,
                    request.verifier_id,
                    _sanitize(request.summary),
                    _now(),
                    action_id,
                ),
            )
            receipt = self._append_receipt(
                conn,
                action["project_id"],
                "verifier.verdict",
                {
                    "action_id": action_id,
                    "attempt_key": request.attempt_key,
                    "result_receipt_id": request.result_receipt_id,
                    "worker_id": action["worker_id"],
                    "verifier_id": request.verifier_id,
                    "verdict": request.verdict,
                    "next_status": next_status,
                    "summary": request.summary,
                    "evidence": [item.model_dump() for item in request.evidence],
                    "request_digest": request_digest,
                },
            )
            if request.verdict == "fail":
                self._fail_undispatched_effects(
                    conn, action, request.attempt_key, "verifier_failed"
                )
                self._contain_exhausted_attempt_paths(conn, action["project_id"])
            project_status = self._sync_project_status(conn, action["project_id"])
            if project_status == "PASSED":
                self._append_receipt(
                    conn,
                    action["project_id"],
                    "project.closed",
                    {"closed_by": request.verifier_id, "basis": "all_actions_passed"},
                )
            project_view = self._project_view(conn, action["project_id"])
        return {"idempotent": False, "receipt": receipt, "project": project_view}

    def _pause_for_effect_approval(
        self,
        conn: sqlite3.Connection,
        action: sqlite3.Row,
        attempt_key: str,
        effect_descriptor: dict[str, Any],
    ) -> ClosureError:
        policy_evidence = [
            {
                "source": "policy://closure/explicit-approval",
                "digest": hashlib.sha256(_canonical(effect_descriptor).encode()).hexdigest(),
                "summary": "Action contract marks this external effect as approval-required",
            }
        ]
        conn.execute(
            "UPDATE actions SET status = 'APPROVAL_PENDING', updated_at = ? WHERE id = ?",
            (_now(), action["id"]),
        )
        obstacle_id = f"obs_{uuid.uuid4().hex[:20]}"
        description = "Effect requires explicit approval"
        next_step = "Provide an approval reference, then resume the action"
        conn.execute(
            """INSERT INTO obstacles
               (id, project_id, action_id, obstacle_class, hard_reason,
                description, evidence_json, next_step, created_at)
               VALUES (?, ?, ?, 'APPROVAL GATE', 'irreversible_approval', ?, ?, ?, ?)""",
            (
                obstacle_id,
                action["project_id"],
                action["id"],
                description,
                _canonical(policy_evidence),
                next_step,
                _now(),
            ),
        )
        self._append_receipt(
            conn,
            action["project_id"],
            "effect.denied",
            {
                "action_id": action["id"],
                "attempt_key": attempt_key,
                "reason": "approval_required",
                "effect": effect_descriptor,
                "obstacle_id": obstacle_id,
                "obstacle_class": "APPROVAL GATE",
                "hard_reason": "irreversible_approval",
                "description": description,
                "evidence": policy_evidence,
                "next_step": next_step,
            },
        )
        self._sync_project_status(conn, action["project_id"])
        return ClosureError(
            "approval_required", "This effect requires explicit approval", 403
        )

    def reserve_effect(self, action_id: str, request: EffectRequest | dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, EffectRequest):
            request = EffectRequest.model_validate(request)
        effect_descriptor = {
            "action_id": action_id,
            "effect_kind": _sanitize(request.effect_kind),
            "target": _sanitize(request.target),
            "payload_digest": request.payload_digest,
        }
        effect_digest = hashlib.sha256(_canonical(effect_descriptor).encode()).hexdigest()
        deferred_error: ClosureError | None = None
        result: dict[str, Any] | None = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM effect_reservations WHERE idempotency_key = ?",
                (request.idempotency_key,),
            ).fetchone()
            if existing:
                if (
                    existing["action_id"] != action_id
                    or existing["attempt_key"] != request.attempt_key
                    or existing["effect_digest"] != effect_digest
                ):
                    raise ClosureError(
                        "idempotency_conflict",
                        "idempotency_key is already bound to a different effect",
                        409,
                    )
                action = self._row(conn, "actions", action_id)
                if (
                    existing["status"] == "FAILED"
                    and action["status"] == "RUNNING"
                    and action["attempt_key"] == request.attempt_key
                ):
                    last_event = None
                    for event in conn.execute(
                        "SELECT kind, payload_json FROM receipts WHERE project_id = ? ORDER BY seq",
                        (action["project_id"],),
                    ).fetchall():
                        try:
                            payload = json.loads(event["payload_json"])
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if (
                            event["kind"] == "effect.not_dispatched"
                            and request.idempotency_key in payload.get("idempotency_keys", [])
                        ):
                            last_event = "not_dispatched"
                        elif (
                            payload.get("idempotency_key") == request.idempotency_key
                            and event["kind"]
                            in {"effect.status_changed", "effect.reactivated"}
                        ):
                            last_event = event["kind"]
                    if last_event == "not_dispatched":
                        conn.execute(
                            """UPDATE effect_reservations
                               SET status = 'RESERVED', adapter_id = NULL, result_digest = NULL,
                                   dispatch_token = NULL, updated_at = ?
                               WHERE idempotency_key = ?""",
                            (_now(), request.idempotency_key),
                        )
                        receipt = self._append_receipt(
                            conn,
                            action["project_id"],
                            "effect.reactivated",
                            {
                                "action_id": action_id,
                                "attempt_key": request.attempt_key,
                                "idempotency_key": request.idempotency_key,
                                "from": "FAILED",
                                "to": "RESERVED",
                                "reason": "same_attempt_resumed",
                            },
                        )
                        reactivated = conn.execute(
                            "SELECT * FROM effect_reservations WHERE idempotency_key = ?",
                            (request.idempotency_key,),
                        ).fetchone()
                        return {
                            "idempotent": False,
                            "reactivated": True,
                            "reservation": {
                                key: value
                                for key, value in dict(reactivated).items()
                                if key != "dispatch_token"
                            },
                            "receipt": receipt,
                            "project": self._project_view(conn, action["project_id"]),
                        }
                return {
                    "idempotent": True,
                    "reservation": {
                        key: value
                        for key, value in dict(existing).items()
                        if key != "dispatch_token"
                    },
                    "project": self._project_view(conn, action["project_id"]),
                }
            existing_adoption = conn.execute(
                "SELECT * FROM effect_adoptions WHERE idempotency_key = ?",
                (request.idempotency_key,),
            ).fetchone()
            if existing_adoption:
                if (
                    existing_adoption["action_id"] != action_id
                    or existing_adoption["attempt_key"] != request.attempt_key
                    or existing_adoption["effect_digest"] != effect_digest
                ):
                    raise ClosureError(
                        "idempotency_conflict",
                        "idempotency_key is already bound to a different effect adoption",
                        409,
                    )
                action = self._row(conn, "actions", action_id)
                return {
                    "idempotent": True,
                    "already_completed": True,
                    "adopted": True,
                    "adoption": dict(existing_adoption),
                    "project": self._project_view(conn, action["project_id"]),
                }
            action = self._row(conn, "actions", action_id)
            project = self._row(conn, "projects", action["project_id"])
            if action["status"] != "RUNNING":
                raise ClosureError(
                    "illegal_transition", f"Effect reservation requires RUNNING, got {action['status']}", 409
                )
            if action["attempt_key"] != request.attempt_key:
                raise ClosureError(
                    "stale_attempt",
                    "Effect does not belong to the action's current attempt",
                    409,
                )
            if action["risk"] == "read_only":
                raise ClosureError(
                    "effect_not_allowed",
                    "Read-only work cannot reserve an external effect",
                    409,
                )
            if bool(action["requires_approval"]) and not request.approval_ref:
                deferred_error = self._pause_for_effect_approval(
                    conn, action, request.attempt_key, effect_descriptor
                )
                conn.commit()
                raise deferred_error
            same_attempt_adoption = conn.execute(
                """SELECT * FROM effect_adoptions
                   WHERE action_id = ? AND attempt_key = ? AND effect_digest = ?""",
                (action_id, request.attempt_key, effect_digest),
            ).fetchone()
            if same_attempt_adoption:
                raise ClosureError(
                    "effect_already_adopted",
                    "This attempt already adopted the completed logical effect",
                    409,
                )
            same_attempt_effect = conn.execute(
                """SELECT * FROM effect_reservations
                   WHERE action_id = ? AND attempt_key = ? AND effect_digest = ?""",
                (action_id, request.attempt_key, effect_digest),
            ).fetchone()
            if same_attempt_effect:
                raise ClosureError(
                    "effect_already_reserved",
                    "This attempt/effect digest already has a reservation under another key",
                    409,
                )
            same_effect = conn.execute(
                """SELECT * FROM effect_reservations
                   WHERE action_id = ? AND effect_digest = ? AND status != 'FAILED'
                   ORDER BY created_at DESC LIMIT 1""",
                (action_id, effect_digest),
            ).fetchone()
            if same_effect:
                if same_effect["status"] == "COMPLETED":
                    receipt = self._append_receipt(
                        conn,
                        action["project_id"],
                        "effect.adopted",
                        {
                            "action_id": action_id,
                            "attempt_key": request.attempt_key,
                            "idempotency_key": request.idempotency_key,
                            "effect_digest": effect_digest,
                            "source_idempotency_key": same_effect["idempotency_key"],
                            "source_result_digest": same_effect["result_digest"],
                            "approval_ref": request.approval_ref,
                        },
                    )
                    conn.execute(
                        """INSERT INTO effect_adoptions
                           (idempotency_key, action_id, attempt_key, effect_digest,
                            source_idempotency_key, approval_ref, receipt_id, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            request.idempotency_key,
                            action_id,
                            request.attempt_key,
                            effect_digest,
                            same_effect["idempotency_key"],
                            _sanitize(request.approval_ref),
                            receipt["id"],
                            _now(),
                        ),
                    )
                    return {
                        "idempotent": False,
                        "already_completed": True,
                        "adopted": True,
                        "reservation": {
                            key: value
                            for key, value in dict(same_effect).items()
                            if key != "dispatch_token"
                        },
                        "adoption": dict(
                            conn.execute(
                                "SELECT * FROM effect_adoptions WHERE idempotency_key = ?",
                                (request.idempotency_key,),
                            ).fetchone()
                        ),
                        "receipt": receipt,
                        "project": self._project_view(conn, action["project_id"]),
                    }
                raise ClosureError(
                    "effect_reconciliation_required",
                    "This logical effect already has an unresolved reservation",
                    409,
                )
            if bool(action["requires_approval"]) and not request.approval_ref:
                policy_evidence = [
                    {
                        "source": "policy://closure/explicit-approval",
                        "digest": hashlib.sha256(
                            _canonical(effect_descriptor).encode()
                        ).hexdigest(),
                        "summary": "Action contract marks this external effect as approval-required",
                    }
                ]
                conn.execute(
                    "UPDATE actions SET status = 'APPROVAL_PENDING', updated_at = ? WHERE id = ?",
                    (_now(), action_id),
                )
                obstacle_id = f"obs_{uuid.uuid4().hex[:20]}"
                conn.execute(
                    """INSERT INTO obstacles
                       (id, project_id, action_id, obstacle_class, hard_reason,
                        description, evidence_json, next_step, created_at)
                       VALUES (?, ?, ?, 'APPROVAL GATE', 'irreversible_approval', ?, ?, ?, ?)""",
                    (
                        obstacle_id,
                        action["project_id"],
                        action_id,
                        "Effect requires explicit approval",
                        _canonical(policy_evidence),
                        "Provide an approval reference, then resume the action",
                        _now(),
                    ),
                )
                self._append_receipt(
                    conn,
                    action["project_id"],
                    "effect.denied",
                    {
                        "action_id": action_id,
                        "attempt_key": request.attempt_key,
                        "reason": "approval_required",
                        "effect": effect_descriptor,
                        "obstacle_id": obstacle_id,
                        "obstacle_class": "APPROVAL GATE",
                        "hard_reason": "irreversible_approval",
                        "description": "Effect requires explicit approval",
                        "evidence": policy_evidence,
                        "next_step": "Provide an approval reference, then resume the action",
                    },
                )
                self._sync_project_status(conn, action["project_id"])
                deferred_error = ClosureError(
                    "approval_required", "This effect requires explicit approval", 403
                )
            elif int(project["reserved_effects"]) >= int(project["max_effects"]):
                conn.execute(
                    "UPDATE actions SET status = 'QUARANTINED', updated_at = ? WHERE id = ?",
                    (_now(), action_id),
                )
                self._append_receipt(
                    conn,
                    action["project_id"],
                    "budget.exhausted",
                    {"action_id": action_id, "budget": "effects"},
                )
                self._sync_project_status(conn, action["project_id"])
                deferred_error = ClosureError(
                    "effect_budget_exhausted", "Effect budget is exhausted", 409
                )
            else:
                receipt = self._append_receipt(
                    conn,
                    action["project_id"],
                    "effect.reserved",
                    {
                        "action_id": action_id,
                        "attempt_key": request.attempt_key,
                        "idempotency_key": request.idempotency_key,
                        "effect": effect_descriptor,
                        "effect_digest": effect_digest,
                        "approval_ref": request.approval_ref,
                    },
                )
                timestamp = _now()
                conn.execute(
                    """INSERT INTO effect_reservations
                       (idempotency_key, action_id, attempt_key, effect_digest, approval_ref,
                        status, adapter_id, result_digest, dispatch_token,
                        receipt_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'RESERVED', NULL, NULL, NULL, ?, ?, ?)""",
                    (
                        request.idempotency_key,
                        action_id,
                        request.attempt_key,
                        effect_digest,
                        _sanitize(request.approval_ref),
                        receipt["id"],
                        timestamp,
                        timestamp,
                    ),
                )
                conn.execute(
                    "UPDATE projects SET reserved_effects = reserved_effects + 1, updated_at = ? WHERE id = ?",
                    (timestamp, action["project_id"]),
                )
                result = {
                    "idempotent": False,
                    "reservation": {
                        "idempotency_key": request.idempotency_key,
                        "action_id": action_id,
                        "attempt_key": request.attempt_key,
                        "effect_digest": effect_digest,
                        "effect": effect_descriptor,
                        "approval_ref": request.approval_ref,
                        "status": "RESERVED",
                        "receipt_id": receipt["id"],
                    },
                    "receipt": receipt,
                    "project": self._project_view(conn, action["project_id"]),
                }
        if deferred_error:
            raise deferred_error
        assert result is not None
        return result

    def mark_effect(
        self, idempotency_key: str, request: EffectStatusRequest | dict[str, Any]
    ) -> dict[str, Any]:
        """Advance a write-ahead effect record without pretending to execute it."""
        if not isinstance(request, EffectStatusRequest):
            request = EffectStatusRequest.model_validate(request)
        terminal = {"COMPLETED", "FAILED", "UNKNOWN"}
        if request.status in terminal and (not request.result_digest or not request.evidence):
            raise ClosureError(
                "effect_evidence_required",
                "Terminal effect status requires a result digest and evidence",
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            reservation = conn.execute(
                "SELECT * FROM effect_reservations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if reservation is None:
                raise ClosureError(
                    "effect_not_found", f"Effect reservation {idempotency_key} does not exist", 404
                )
            current = reservation["status"]
            if current == request.status:
                same_actor = reservation["adapter_id"] == request.adapter_id
                same_result = reservation["result_digest"] == request.result_digest
                if same_actor and same_result:
                    if (
                        current == "DISPATCHED"
                        and request.dispatch_token
                        and request.dispatch_token != reservation["dispatch_token"]
                    ):
                        raise ClosureError(
                            "dispatch_token_required",
                            "A replayed dispatch supplied the wrong dispatch token",
                            403,
                        )
                    action = self._row(conn, "actions", reservation["action_id"])
                    return {
                        "idempotent": True,
                        "reservation": dict(reservation),
                        "project": self._project_view(conn, action["project_id"]),
                    }
                raise ClosureError(
                    "idempotency_conflict",
                    "Effect status is already bound to different evidence or adapter",
                    409,
                )
            allowed = {
                "RESERVED": {"DISPATCHED", "FAILED"},
                "DISPATCHED": {"COMPLETED", "FAILED", "UNKNOWN"},
                "COMPLETED": set(),
                "FAILED": set(),
                "UNKNOWN": set(),
            }
            if request.status not in allowed.get(current, set()):
                raise ClosureError(
                    "illegal_effect_transition",
                    f"Cannot transition an effect from {current} to {request.status}",
                    409,
                )
            if reservation["adapter_id"] and reservation["adapter_id"] != request.adapter_id:
                raise ClosureError(
                    "adapter_mismatch",
                    "Only the dispatching adapter may finalize this effect",
                    403,
                )
            action = self._row(conn, "actions", reservation["action_id"])
            project = self._row(conn, "projects", action["project_id"])
            timestamp = _now()
            if current == "RESERVED" and request.status == "DISPATCHED":
                if reservation["attempt_key"] != action["attempt_key"]:
                    raise ClosureError(
                        "stale_attempt",
                        "An effect from an older attempt cannot be dispatched",
                        409,
                    )
                if not project["halted_reason"] and action["status"] not in {"RUNNING", "VERIFYING"}:
                    raise ClosureError(
                        "illegal_effect_transition",
                        f"Effect dispatch requires an active attempt, got {action['status']}",
                        409,
                    )
            if (
                project["halted_reason"]
                and current == "RESERVED"
                and request.status == "DISPATCHED"
            ):
                raise ClosureError(
                    "operator_halt",
                    "A halted project may reconcile dispatched effects but cannot dispatch a new one",
                    409,
                )
            dispatch_token = reservation["dispatch_token"]
            if current == "RESERVED" and request.status == "DISPATCHED":
                dispatch_token = f"dsp_{uuid.uuid4().hex}"
            if current == "DISPATCHED" and request.status in terminal:
                if not request.dispatch_token or request.dispatch_token != dispatch_token:
                    raise ClosureError(
                        "dispatch_token_required",
                        "Terminal reconciliation requires the matching dispatch token",
                        403,
                    )
            conn.execute(
                """UPDATE effect_reservations
                   SET status = ?, adapter_id = ?, result_digest = ?, dispatch_token = ?, updated_at = ?
                   WHERE idempotency_key = ?""",
                (
                    request.status,
                    request.adapter_id,
                    request.result_digest,
                    dispatch_token,
                    timestamp,
                    idempotency_key,
                ),
            )
            receipt = self._append_receipt(
                conn,
                action["project_id"],
                "effect.status_changed",
                {
                    "action_id": action["id"],
                    "attempt_key": reservation["attempt_key"],
                    "idempotency_key": idempotency_key,
                    "from": current,
                    "to": request.status,
                    "adapter_id": request.adapter_id,
                    "result_digest": request.result_digest,
                    "evidence": [item.model_dump() for item in request.evidence],
                },
            )
            updated = conn.execute(
                "SELECT * FROM effect_reservations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            project_view = self._project_view(conn, action["project_id"])
        return {
            "idempotent": False,
            "reservation": dict(updated),
            "receipt": receipt,
            "project": project_view,
        }

    def reconcile_unknown_effect(
        self, idempotency_key: str, request: EffectReconcileRequest | dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve a may-have-applied effect only through an explicit operator decision."""
        if not isinstance(request, EffectReconcileRequest):
            request = EffectReconcileRequest.model_validate(request)
        safe_request = _sanitize(request.model_dump())
        request_digest = hashlib.sha256(_canonical(safe_request).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            reservation = conn.execute(
                "SELECT * FROM effect_reservations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if reservation is None:
                raise ClosureError(
                    "effect_not_found", f"Effect reservation {idempotency_key} does not exist", 404
                )
            action = self._row(conn, "actions", reservation["action_id"])
            if reservation["status"] not in {"DISPATCHED", "UNKNOWN"}:
                prior_rows = conn.execute(
                    """SELECT * FROM receipts
                       WHERE project_id = ? AND kind = 'effect.operator_reconciled'
                       ORDER BY seq DESC""",
                    (action["project_id"],),
                ).fetchall()
                for prior in prior_rows:
                    try:
                        payload = json.loads(prior["payload_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if payload.get("idempotency_key") != idempotency_key:
                        continue
                    legacy_match = all(
                        payload.get(field) == safe_request.get(request_field)
                        for field, request_field in (
                            ("operator_id", "operator_id"),
                            ("authorization_ref", "authorization_ref"),
                            ("to", "decision"),
                            ("result_digest", "result_digest"),
                            ("evidence", "evidence"),
                        )
                    )
                    if payload.get("request_digest") == request_digest or (
                        payload.get("request_digest") is None and legacy_match
                    ):
                        return {
                            "idempotent": True,
                            "reservation": dict(reservation),
                            "receipt": self._receipt_view(prior),
                            "project": self._project_view(conn, action["project_id"]),
                        }
                    raise ClosureError(
                        "idempotency_conflict",
                        "This effect already has a different operator reconciliation",
                        409,
                    )
                raise ClosureError(
                    "illegal_effect_transition",
                    "Operator reconciliation requires a DISPATCHED or UNKNOWN effect, "
                    f"got {reservation['status']}",
                    409,
                )
            timestamp = _now()
            conn.execute(
                """UPDATE effect_reservations SET status = ?, result_digest = ?, updated_at = ?
                   WHERE idempotency_key = ?""",
                (request.decision, request.result_digest, timestamp, idempotency_key),
            )
            receipt = self._append_receipt(
                conn,
                action["project_id"],
                "effect.operator_reconciled",
                {
                    "action_id": action["id"],
                    "attempt_key": reservation["attempt_key"],
                    "idempotency_key": idempotency_key,
                    "from": reservation["status"],
                    "to": request.decision,
                    "operator_id": request.operator_id,
                    "authorization_ref": request.authorization_ref,
                    "result_digest": request.result_digest,
                    "evidence": [item.model_dump() for item in request.evidence],
                    "request_digest": request_digest,
                },
            )
            updated = conn.execute(
                "SELECT * FROM effect_reservations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            project_view = self._project_view(conn, action["project_id"])
        return {
            "idempotent": False,
            "reservation": dict(updated),
            "receipt": receipt,
            "project": project_view,
        }

    def halt_project(self, project_id: str, request: HaltRequest | dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, HaltRequest):
            request = HaltRequest.model_validate(request)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._row(conn, "projects", project_id)
            timestamp = _now()
            conn.execute(
                "UPDATE projects SET status = 'QUARANTINED', halted_reason = ?, updated_at = ? WHERE id = ?",
                (_sanitize(request.reason), timestamp, project_id),
            )
            conn.execute(
                "UPDATE actions SET status = 'QUARANTINED', updated_at = ? WHERE project_id = ? AND status != 'PASSED'",
                (timestamp, project_id),
            )
            receipt = self._append_receipt(
                conn,
                project_id,
                "operator.halt",
                {"operator_id": request.operator_id, "reason": request.reason},
            )
            project_view = self._project_view(conn, project_id)
        return {"receipt": receipt, "project": project_view}

    def receipts(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._connect() as conn:
            self._row(conn, "projects", project_id)
            rows = conn.execute(
                "SELECT * FROM receipts WHERE project_id = ? ORDER BY seq DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [
            {
                "seq": row["seq"],
                "id": row["id"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "prev_hash": row["prev_hash"],
                "hash": row["receipt_hash"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def verify_receipt_chain(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM receipts ORDER BY seq").fetchall()
        expected_prev = "GENESIS"
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                return {
                    "ok": False,
                    "failed_seq": row["seq"],
                    "count": len(rows),
                    "reason": "invalid_receipt_payload",
                }
            envelope = {
                "project_id": row["project_id"],
                "kind": row["kind"],
                "payload": payload,
                "prev_hash": row["prev_hash"],
                "created_at": row["created_at"],
            }
            expected_hash = hashlib.sha256(_canonical(envelope).encode()).hexdigest()
            if row["prev_hash"] != expected_prev or row["receipt_hash"] != expected_hash:
                return {
                    "ok": False,
                    "failed_seq": row["seq"],
                    "count": len(rows),
                    "reason": "hash_chain_mismatch",
                }
            expected_prev = row["receipt_hash"]
        return {"ok": True, "failed_seq": None, "count": len(rows), "reason": None}

    def _audit_materialized_state(self, conn: sqlite3.Connection) -> dict[str, int]:
        """Cross-check mutable projections against the append-only protocol record."""
        projects = {row["id"]: row for row in conn.execute("SELECT * FROM projects")}
        actions = {row["id"]: row for row in conn.execute("SELECT * FROM actions")}
        project_requests = {
            row["request_key"]: row for row in conn.execute("SELECT * FROM project_requests")
        }
        effects = {
            row["idempotency_key"]: row
            for row in conn.execute("SELECT * FROM effect_reservations")
        }
        adoptions = {
            row["idempotency_key"]: row for row in conn.execute("SELECT * FROM effect_adoptions")
        }
        claims = list(conn.execute("SELECT * FROM claims"))
        receipt_rows = list(conn.execute("SELECT * FROM receipts ORDER BY seq"))

        malformed_receipts = 0
        receipts: list[tuple[sqlite3.Row, dict[str, Any]]] = []
        receipts_by_id: dict[str, tuple[sqlite3.Row, dict[str, Any]]] = {}
        for row in receipt_rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                malformed_receipts += 1
                continue
            receipts.append((row, payload))
            receipts_by_id[row["id"]] = (row, payload)

        contracts: dict[str, dict[str, Any]] = {}
        schema_contract_binding_mismatches = 0
        for row, payload in receipts:
            if row["kind"] in {"contract.ratified", "schema.contract_bound"}:
                contracts[row["project_id"]] = payload
            if row["kind"] == "schema.contract_bound":
                source_ids = payload.get("source_action_ids")
                bound_ids = payload.get("action_ids")
                source_receipt = receipts_by_id.get(payload.get("source_receipt_id"))
                source_payload = source_receipt[1] if source_receipt else None
                if (
                    isinstance(source_ids, list)
                    and source_ids
                    and (
                        not isinstance(bound_ids, list)
                        or {str(item) for item in source_ids}
                        != {str(item) for item in bound_ids}
                    )
                ):
                    schema_contract_binding_mismatches += 1
                if source_payload is None:
                    schema_contract_binding_mismatches += 1
                else:
                    for field in (
                        "name",
                        "objective",
                        "closed_when",
                        "proposer_id",
                        "ratifier_id",
                    ):
                        if field in source_payload and source_payload[field] != payload.get(field):
                            schema_contract_binding_mismatches += 1
                    source_budgets = source_payload.get("budgets")
                    bound_budgets = payload.get("budgets")
                    if isinstance(source_budgets, dict):
                        for budget in ("max_attempts", "max_effects"):
                            if budget in source_budgets and source_budgets[budget] != (
                                bound_budgets or {}
                            ).get(budget):
                                schema_contract_binding_mismatches += 1
        project_contract_projection_mismatches = 0
        action_contract_projection_mismatches = 0
        for project_id, project in projects.items():
            contract = contracts.get(project_id)
            try:
                closed_when = json.loads(project["closed_when_json"])
            except (TypeError, json.JSONDecodeError):
                closed_when = None
            if (
                contract is None
                or project["name"] != contract.get("name")
                or project["objective"] != contract.get("objective")
                or closed_when != contract.get("closed_when")
                or project["proposer_id"] != contract.get("proposer_id")
                or project["ratifier_id"] != contract.get("ratifier_id")
                or int(project["max_attempts"])
                != int((contract.get("budgets") or {}).get("max_attempts", -1))
                or int(project["max_effects"])
                != int((contract.get("budgets") or {}).get("max_effects", -1))
            ):
                project_contract_projection_mismatches += 1
            expected_actions = {
                item.get("id"): item
                for item in (contract or {}).get("actions", [])
                if isinstance(item, dict) and item.get("id")
            }
            actual_actions = {
                action_id: action
                for action_id, action in actions.items()
                if action["project_id"] == project_id
            }
            ratified_action_ids = {
                str(item) for item in (contract or {}).get("action_ids", [])
            }
            action_contract_projection_mismatches += len(
                ratified_action_ids.symmetric_difference(actual_actions)
            )
            action_contract_projection_mismatches += len(
                set(expected_actions).symmetric_difference(actual_actions)
            )
            for action_id in set(expected_actions).intersection(actual_actions):
                expected = expected_actions[action_id]
                actual = actual_actions[action_id]
                try:
                    success_criteria = json.loads(actual["success_criteria_json"])
                except (TypeError, json.JSONDecodeError):
                    success_criteria = None
                if (
                    actual["title"] != expected.get("title")
                    or success_criteria != expected.get("success_criteria")
                    or float(actual["value"]) != float(expected.get("value", float("nan")))
                    or float(actual["probability"])
                    != float(expected.get("probability", float("nan")))
                    or float(actual["remaining_effort"])
                    != float(expected.get("remaining_effort", float("nan")))
                    or int(actual["max_attempts"]) != int(expected.get("max_attempts", -1))
                    or actual["risk"] != expected.get("risk")
                    or bool(actual["requires_approval"])
                    != bool(expected.get("requires_approval"))
                ):
                    action_contract_projection_mismatches += 1

        derived_states: dict[str, str | None] = {action_id: None for action_id in actions}
        derived_workers: dict[str, str | None] = {action_id: None for action_id in actions}
        derived_attempts: dict[str, str | None] = {action_id: None for action_id in actions}
        derived_verifiers: dict[str, str | None] = {action_id: None for action_id in actions}
        project_action_ids: dict[str, set[str]] = defaultdict(set)
        for action_id, action in actions.items():
            project_action_ids[action["project_id"]].add(action_id)
        for row, payload in receipts:
            kind = row["kind"]
            if kind == "contract.ratified":
                for action_id in payload.get("action_ids", []):
                    if action_id in derived_states:
                        derived_states[action_id] = "READY"
            elif kind in {"action.claimed", "action.resumed"}:
                action_id = payload.get("action_id")
                if action_id in derived_states:
                    derived_states[action_id] = "RUNNING"
                    derived_workers[action_id] = payload.get("worker_id")
                    derived_attempts[action_id] = payload.get("attempt_key") or payload.get(
                        "claim_key"
                    )
                    if kind == "action.claimed":
                        derived_verifiers[action_id] = None
            elif kind == "worker.result":
                action_id = payload.get("action_id")
                if action_id in derived_states and payload.get("next_status") in ACTION_STATES:
                    derived_states[action_id] = payload["next_status"]
            elif kind in {"verifier.verdict", "schema.verdict_bound"}:
                action_id = payload.get("action_id")
                if action_id in derived_states:
                    if payload.get("next_status") in ACTION_STATES:
                        derived_states[action_id] = payload["next_status"]
                    derived_verifiers[action_id] = payload.get("verifier_id")
            elif kind == "effect.denied" and payload.get("reason") == "approval_required":
                action_id = payload.get("action_id")
                if action_id in derived_states:
                    derived_states[action_id] = "APPROVAL_PENDING"
            elif kind == "budget.paths_contained":
                for action_id in payload.get("action_ids", []):
                    if action_id in derived_states:
                        derived_states[action_id] = "QUARANTINED"
            elif kind == "budget.exhausted":
                action_id = payload.get("action_id")
                if action_id in derived_states:
                    derived_states[action_id] = "QUARANTINED"
            elif kind == "schema.action_contained":
                action_id = payload.get("action_id")
                if action_id in derived_states:
                    derived_states[action_id] = "QUARANTINED"
            elif kind == "operator.halt":
                for action_id in project_action_ids[row["project_id"]]:
                    if derived_states[action_id] != "PASSED":
                        derived_states[action_id] = "QUARANTINED"

        action_state_projection_mismatches = sum(
            derived_states[action_id] != action["status"]
            for action_id, action in actions.items()
        )

        expected_obstacles: dict[str, dict[str, Any]] = {}
        for row, payload in receipts:
            if row["kind"] in {"action.claimed", "action.resumed"}:
                action_id = payload.get("action_id")
                for expected in expected_obstacles.values():
                    if expected["action_id"] == action_id:
                        expected["active"] = 0
                continue
            if row["kind"] == "worker.result" and payload.get("obstacle_id"):
                expected_obstacles[payload["obstacle_id"]] = {
                    "project_id": row["project_id"],
                    "action_id": payload.get("action_id"),
                    "obstacle_class": payload.get("obstacle_class"),
                    "hard_reason": payload.get("hard_reason"),
                    "description": payload.get("summary"),
                    "evidence": payload.get("evidence"),
                    "next_step": payload.get("next_step"),
                    "active": 1,
                }
            elif row["kind"] == "effect.denied" and payload.get("obstacle_id"):
                expected_obstacles[payload["obstacle_id"]] = {
                    "project_id": row["project_id"],
                    "action_id": payload.get("action_id"),
                    "obstacle_class": payload.get("obstacle_class"),
                    "hard_reason": payload.get("hard_reason"),
                    "description": payload.get("description"),
                    "evidence": payload.get("evidence"),
                    "next_step": payload.get("next_step"),
                    "active": 1,
                }
        obstacle_rows = {row["id"]: row for row in conn.execute("SELECT * FROM obstacles")}
        obstacle_projection_mismatches = len(
            set(obstacle_rows).symmetric_difference(expected_obstacles)
        )
        for obstacle_id in set(obstacle_rows).intersection(expected_obstacles):
            actual = obstacle_rows[obstacle_id]
            expected = expected_obstacles[obstacle_id]
            try:
                actual_evidence = json.loads(actual["evidence_json"])
            except (TypeError, json.JSONDecodeError):
                obstacle_projection_mismatches += 1
                continue
            if (
                actual["project_id"] != expected["project_id"]
                or actual["action_id"] != expected["action_id"]
                or actual["obstacle_class"] != expected["obstacle_class"]
                or actual["hard_reason"] != expected["hard_reason"]
                or actual["description"] != expected["description"]
                or actual_evidence != expected["evidence"]
                or actual["next_step"] != expected["next_step"]
                or int(actual["active"]) != expected["active"]
            ):
                obstacle_projection_mismatches += 1
        action_identity_projection_mismatches = sum(
            derived_workers[action_id] != action["worker_id"]
            or derived_attempts[action_id] != action["attempt_key"]
            or derived_verifiers[action_id] != action["verifier_id"]
            for action_id, action in actions.items()
        )

        claim_receipts = [item for item in receipts if item[0]["kind"] == "action.claimed"]
        action_claims = Counter(payload.get("action_id") for _, payload in claim_receipts)
        project_claims = Counter(row["project_id"] for row, _ in claim_receipts)
        effect_receipts = [item for item in receipts if item[0]["kind"] == "effect.reserved"]
        project_effects = Counter(row["project_id"] for row, _ in effect_receipts)
        receipt_effect_keys = {
            payload.get("idempotency_key")
            for _, payload in effect_receipts
            if payload.get("idempotency_key")
        }
        effect_row_bijection_mismatches = len(
            receipt_effect_keys.symmetric_difference(effects)
        )
        receipt_claim_keys = {
            payload.get("attempt_key") or payload.get("claim_key")
            for _, payload in claim_receipts
            if payload.get("attempt_key") or payload.get("claim_key")
        }
        claim_row_bijection_mismatches = len(
            receipt_claim_keys.symmetric_difference({row["claim_key"] for row in claims})
        )
        request_bindings: dict[str, list[tuple[str | None, str | None]]] = defaultdict(list)
        for row, payload in receipts:
            if row["kind"] not in {"project.request_bound", "schema.request_bound"}:
                continue
            request_key = payload.get("request_key")
            if request_key:
                request_bindings[str(request_key)].append(
                    (payload.get("project_id"), payload.get("request_digest"))
                )
        project_request_binding_mismatches = len(
            set(request_bindings).symmetric_difference(project_requests)
        )
        for request_key in set(request_bindings).intersection(project_requests):
            row = project_requests[request_key]
            expected = (row["project_id"], row["request_digest"])
            distinct_bindings = set(request_bindings[request_key])
            if distinct_bindings != {expected}:
                project_request_binding_mismatches += 1

        action_attempt_counter_mismatches = sum(
            int(action["attempt_count"]) != action_claims[action_id]
            for action_id, action in actions.items()
        )
        project_attempt_counter_mismatches = sum(
            int(project["spent_attempts"]) != project_claims[project_id]
            for project_id, project in projects.items()
        )
        project_effect_counter_mismatches = sum(
            int(project["reserved_effects"]) != project_effects[project_id]
            for project_id, project in projects.items()
        )

        claim_receipt_mismatches = 0
        for claim in claims:
            receipt = receipts_by_id.get(claim["receipt_id"])
            if receipt is None:
                claim_receipt_mismatches += 1
                continue
            row, payload = receipt
            if (
                row["kind"] not in {"action.claimed", "schema.claim_bound"}
                or payload.get("action_id") != claim["action_id"]
                or payload.get("attempt_key") != claim["claim_key"]
                or payload.get("worker_id") != claim["worker_id"]
            ):
                claim_receipt_mismatches += 1

        result_binding_mismatches = 0
        invalid_pass_receipts = 0
        for action_id, action in actions.items():
            result_receipt = None
            if action["result_receipt_id"]:
                result_receipt = receipts_by_id.get(action["result_receipt_id"])
                if result_receipt is None:
                    result_binding_mismatches += 1
                else:
                    row, payload = result_receipt
                    if (
                        row["kind"] not in {"worker.result", "schema.result_bound"}
                        or row["project_id"] != action["project_id"]
                        or payload.get("action_id") != action_id
                        or payload.get("attempt_key") != action["attempt_key"]
                        or payload.get("worker_id") != action["worker_id"]
                    ):
                        result_binding_mismatches += 1
            if action["status"] != "PASSED":
                continue
            matching_verdict = any(
                row["kind"] in {"verifier.verdict", "schema.verdict_bound"}
                and row["project_id"] == action["project_id"]
                and payload.get("action_id") == action_id
                and payload.get("attempt_key") == action["attempt_key"]
                and payload.get("result_receipt_id") == action["result_receipt_id"]
                and payload.get("worker_id") == action["worker_id"]
                and payload.get("verifier_id") == action["verifier_id"]
                and payload.get("verdict") == "pass"
                and payload.get("next_status") == "PASSED"
                for row, payload in receipts
            )
            if result_receipt is None or not matching_verdict:
                invalid_pass_receipts += 1

        actions_by_project: dict[str, list[str]] = defaultdict(list)
        for action in actions.values():
            actions_by_project[action["project_id"]].append(action["status"])
        project_state_mismatches = 0
        missing_closure_receipts = 0
        missing_halt_receipts = 0
        contract_integrity_projects = {
            row["project_id"]
            for row, _payload in receipts
            if row["kind"] in {
                "contract.integrity_violation",
                "effect.integrity_violation",
            }
        }
        for project_id, project in projects.items():
            ratified_ids = set((contracts.get(project_id) or {}).get("action_ids", []))
            actual_ids = {
                action_id
                for action_id, action in actions.items()
                if action["project_id"] == project_id
            }
            expected = (
                self._derived_project_status(project, actions_by_project[project_id])
                if (
                    ratified_ids
                    and ratified_ids == actual_ids
                    and project_id not in contract_integrity_projects
                )
                else "QUARANTINED"
            )
            if project["status"] != expected:
                project_state_mismatches += 1
            if project["status"] == "PASSED" and not any(
                row["kind"] == "project.closed" and row["project_id"] == project_id
                for row, _ in receipts
            ):
                missing_closure_receipts += 1
            if project["halted_reason"] and not any(
                row["kind"] == "operator.halt" and row["project_id"] == project_id
                for row, _ in receipts
            ):
                missing_halt_receipts += 1

        effect_receipt_mismatches = 0
        effect_transition_mismatches = 0
        effect_metadata_mismatches = 0
        effect_event_identity_mismatches = 0
        effect_events: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for row, payload in receipts:
            key = payload.get("idempotency_key")
            if key:
                effect_events[str(key)].append((row["kind"], payload))
            if row["kind"] == "effect.not_dispatched":
                for item in payload.get("idempotency_keys", []):
                    effect_events[str(item)].append((row["kind"], payload))
        for key, effect in effects.items():
            reservation_receipt = receipts_by_id.get(effect["receipt_id"])
            if reservation_receipt is None:
                effect_receipt_mismatches += 1
            else:
                row, payload = reservation_receipt
                if (
                    row["kind"] not in {"effect.reserved", "schema.effect_bound"}
                    or payload.get("idempotency_key") != key
                    or payload.get("action_id") != effect["action_id"]
                    or payload.get("attempt_key") != effect["attempt_key"]
                    or payload.get("effect_digest") != effect["effect_digest"]
                    or payload.get("approval_ref") != effect["approval_ref"]
                ):
                    effect_receipt_mismatches += 1
                provenance_cursor = reservation_receipt
                seen_provenance: set[str] = set()
                while provenance_cursor[0]["kind"] == "schema.effect_bound":
                    source_receipt_id = provenance_cursor[1].get("source_receipt_id")
                    if (
                        not isinstance(source_receipt_id, str)
                        or source_receipt_id in seen_provenance
                        or source_receipt_id not in receipts_by_id
                    ):
                        effect_receipt_mismatches += 1
                        break
                    seen_provenance.add(source_receipt_id)
                    provenance_cursor = receipts_by_id[source_receipt_id]
                    if any(
                        field in provenance_cursor[1]
                        and provenance_cursor[1].get(field) != effect[field]
                        for field in (
                            "idempotency_key",
                            "action_id",
                            "attempt_key",
                            "effect_digest",
                            "approval_ref",
                        )
                    ):
                        effect_receipt_mismatches += 1
                        break
            derived = "RESERVED"
            derived_adapter = None
            derived_result_digest = None
            invalid_transition = False
            for kind, payload in effect_events[key]:
                if (
                    (payload.get("action_id") is not None and payload.get("action_id") != effect["action_id"])
                    or (
                        payload.get("attempt_key") is not None
                        and payload.get("attempt_key") != effect["attempt_key"]
                    )
                ):
                    effect_event_identity_mismatches += 1
                if kind == "effect.reserved":
                    continue
                if kind == "effect.status_changed":
                    if payload.get("from") != derived:
                        invalid_transition = True
                        break
                    derived = str(payload.get("to"))
                    derived_adapter = payload.get("adapter_id")
                    derived_result_digest = payload.get("result_digest")
                elif kind == "effect.not_dispatched":
                    if derived != "RESERVED":
                        invalid_transition = True
                        break
                    derived = "FAILED"
                    derived_adapter = None
                    derived_result_digest = payload.get("result_digest")
                elif kind == "effect.operator_reconciled":
                    if derived not in {"DISPATCHED", "UNKNOWN"} or payload.get("from") != derived:
                        invalid_transition = True
                        break
                    derived = str(payload.get("to"))
                    derived_result_digest = payload.get("result_digest")
                elif kind == "effect.migration_quarantined":
                    if derived != "RESERVED" or payload.get("from") != "RESERVED":
                        invalid_transition = True
                        break
                    derived = "UNKNOWN"
                    derived_adapter = None
                    derived_result_digest = payload.get("result_digest")
                elif kind == "effect.reactivated":
                    if derived != "FAILED" or payload.get("from") != "FAILED":
                        invalid_transition = True
                        break
                    derived = "RESERVED"
                    derived_adapter = None
                    derived_result_digest = None
            if invalid_transition or derived != effect["status"]:
                effect_transition_mismatches += 1
            if (
                derived_adapter != effect["adapter_id"]
                or derived_result_digest != effect["result_digest"]
            ):
                effect_metadata_mismatches += 1

        adoption_receipts = {
            payload.get("idempotency_key"): (row, payload)
            for row, payload in receipts
            if row["kind"] == "effect.adopted" and payload.get("idempotency_key")
        }
        effect_adoption_mismatches = len(set(adoption_receipts).symmetric_difference(adoptions))
        for key in set(adoption_receipts).intersection(adoptions):
            row, payload = adoption_receipts[key]
            adoption = adoptions[key]
            source = effects.get(adoption["source_idempotency_key"])
            if (
                row["id"] != adoption["receipt_id"]
                or payload.get("action_id") != adoption["action_id"]
                or payload.get("attempt_key") != adoption["attempt_key"]
                or payload.get("effect_digest") != adoption["effect_digest"]
                or payload.get("source_idempotency_key")
                != adoption["source_idempotency_key"]
                or payload.get("approval_ref") != adoption["approval_ref"]
                or source is None
                or source["status"] != "COMPLETED"
                or source["effect_digest"] != adoption["effect_digest"]
                or source["action_id"] != adoption["action_id"]
            ):
                effect_adoption_mismatches += 1

        approval_pass_mismatches = sum(
            action["status"] == "PASSED"
            and bool(action["requires_approval"])
            and not any(
                effect["action_id"] == action_id
                and effect["attempt_key"] == action["attempt_key"]
                and effect["status"] == "COMPLETED"
                and bool(effect["approval_ref"])
                for effect in effects.values()
            )
            and not any(
                adoption["action_id"] == action_id
                and adoption["attempt_key"] == action["attempt_key"]
                and bool(adoption["approval_ref"])
                and adoption["source_idempotency_key"] in effects
                and effects[adoption["source_idempotency_key"]]["status"] == "COMPLETED"
                and bool(effects[adoption["source_idempotency_key"]]["approval_ref"])
                for adoption in adoptions.values()
            )
            for action_id, action in actions.items()
        )

        return {
            "malformed_receipts": malformed_receipts,
            "invalid_action_states": sum(
                action["status"] not in ACTION_STATES for action in actions.values()
            ),
            "invalid_project_states": sum(
                project["status"] not in PROJECT_STATES for project in projects.values()
            ),
            "invalid_effect_states": sum(
                effect["status"] not in EFFECT_STATES for effect in effects.values()
            ),
            "action_attempt_counter_mismatches": action_attempt_counter_mismatches,
            "project_attempt_counter_mismatches": project_attempt_counter_mismatches,
            "project_effect_counter_mismatches": project_effect_counter_mismatches,
            "claim_receipt_mismatches": claim_receipt_mismatches,
            "result_binding_mismatches": result_binding_mismatches,
            "invalid_pass_receipts": invalid_pass_receipts,
            "project_state_mismatches": project_state_mismatches,
            "missing_closure_receipts": missing_closure_receipts,
            "missing_halt_receipts": missing_halt_receipts,
            "effect_receipt_mismatches": effect_receipt_mismatches,
            "effect_transition_mismatches": effect_transition_mismatches,
            "approval_pass_mismatches": approval_pass_mismatches,
            "action_state_projection_mismatches": action_state_projection_mismatches,
            "action_identity_projection_mismatches": action_identity_projection_mismatches,
            "obstacle_projection_mismatches": obstacle_projection_mismatches,
            "project_contract_projection_mismatches": project_contract_projection_mismatches,
            "action_contract_projection_mismatches": action_contract_projection_mismatches,
            "schema_contract_binding_mismatches": schema_contract_binding_mismatches,
            "effect_row_bijection_mismatches": effect_row_bijection_mismatches,
            "claim_row_bijection_mismatches": claim_row_bijection_mismatches,
            "project_request_binding_mismatches": project_request_binding_mismatches,
            "effect_adoption_mismatches": effect_adoption_mismatches,
            "effect_metadata_mismatches": effect_metadata_mismatches,
            "effect_event_identity_mismatches": effect_event_identity_mismatches,
        }

    def health(self) -> dict[str, Any]:
        with self._connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            schema_row = conn.execute(
                "SELECT version FROM closure_schema_meta WHERE singleton = 1"
            ).fetchone()
            schema_version = int(schema_row["version"]) if schema_row else 0
            invalid_self_verification = conn.execute(
                """SELECT COUNT(*) FROM actions
                   WHERE status = 'PASSED' AND (verifier_id IS NULL OR verifier_id = worker_id)"""
            ).fetchone()[0]
            unapproved_effects = conn.execute(
                """SELECT COUNT(*) FROM effect_reservations e
                   JOIN actions a ON a.id = e.action_id
                   WHERE a.requires_approval = 1 AND (e.approval_ref IS NULL OR e.approval_ref = '')"""
            ).fetchone()[0]
            open_approval_actions = conn.execute(
                """SELECT COUNT(*) FROM actions
                   WHERE requires_approval = 1 AND status != 'PASSED'"""
            ).fetchone()[0]
            unresolved_effects = conn.execute(
                """SELECT COUNT(*) FROM effect_reservations
                   WHERE status IN ('DISPATCHED', 'UNKNOWN')"""
            ).fetchone()[0]
            stale_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
            stale_reservations = conn.execute(
                """SELECT COUNT(*) FROM effect_reservations effect
                   WHERE effect.status = 'RESERVED' AND COALESCE(
                       (
                           SELECT MAX(receipt.created_at) FROM receipts receipt
                           WHERE receipt.kind IN (
                               'effect.reserved', 'effect.reactivated', 'schema.effect_bound'
                           )
                           AND json_extract(receipt.payload_json, '$.idempotency_key') =
                               effect.idempotency_key
                       ),
                       effect.created_at
                   ) < ?""",
                (stale_cutoff,),
            ).fetchone()[0]
            state_rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM projects GROUP BY status"
            ).fetchall()
            materialization = self._audit_materialized_state(conn)
        chain = self.verify_receipt_chain()
        authority = _authority_health(open_approval_actions)
        materialization_ok = all(value == 0 for value in materialization.values())
        policy_ok = (
            invalid_self_verification == 0
            and unapproved_effects == 0
            and authority["ok"]
            and materialization_ok
        )
        workflow_ok = chain["ok"] and stale_reservations == 0 and materialization_ok
        outcome_ok = unresolved_effects == 0
        schema_ok = schema_version == CLOSURE_SCHEMA_VERSION
        all_ok = integrity == "ok" and schema_ok and policy_ok and workflow_ok and outcome_ok
        return {
            "status": "ok" if all_ok else "degraded",
            "layers": {
                "mechanism": {
                    "ok": integrity == "ok" and schema_ok,
                    "sqlite_integrity": integrity,
                    "schema_version": schema_version,
                    "expected_schema_version": CLOSURE_SCHEMA_VERSION,
                },
                "control": {
                    "ok": policy_ok,
                    "self_verified_passes": invalid_self_verification,
                    "unapproved_effects": unapproved_effects,
                    "materialization_ok": materialization_ok,
                    "materialization": materialization,
                    "authority": authority,
                },
                "workflow": {
                    "ok": workflow_ok,
                    "receipt_chain": chain,
                    "stale_reservations": stale_reservations,
                    "states": {row["status"]: row["n"] for row in state_rows},
                },
                "outcome": {
                    "ok": outcome_ok,
                    "unresolved_effects": unresolved_effects,
                    "note": "Outcome conformance also requires external acceptance evidence",
                },
            },
            "conformance": "UNSCORED",
        }


def _evidence(label: str) -> list[dict[str, str]]:
    return [
        {
            "source": f"fixture://{label}",
            "digest": hashlib.sha256(label.encode()).hexdigest(),
            "summary": label,
        }
    ]


def _legacy_transition_fixture_self_test() -> dict[str, Any]:
    """Compatibility alias for callers of the pre-v2 private fixture helper."""
    return run_five_fixture_self_test()

    # Retained below only as historical fixture documentation; the v2 runner is
    # the executable source of truth.
    cases: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="closure-self-test-") as directory:
        db_path = Path(directory) / "closure.sqlite3"
        store = ClosureStore(db_path)

        def make(name: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
            return store.create_project(
                {
                    "name": name,
                    "objective": f"Close {name}",
                    "closed_when": ["Every action has independent passing evidence"],
                    "proposer_id": "fixture-proposer",
                    "ratifier_id": "fixture-ratifier",
                    "actions": actions,
                    "max_attempts": 10,
                    "max_effects": 5,
                }
            )

        # 1. Reversible ambiguity remains retryable; it cannot become a hard blocker.
        project = make(
            "reversible-ambiguity",
            [{"title": "Resolve ambiguity", "success_criteria": ["decision tested"]}],
        )
        action = project["actions"][0]
        store.claim_action(action["id"], {"worker_id": "w1", "claim_key": "ambiguity-1"})
        result = store.record_result(
            action["id"],
            {
                "worker_id": "w1",
                "outcome": "blocked",
                "summary": "Several reversible choices remain",
                "evidence": _evidence("ambiguity"),
                "obstacle_class": "UNCERTAINTY",
                "next_step": "Try the cheapest reversible choice",
            },
        )
        ok = result["project"]["actions"][0]["status"] == "FAILED_RETRYABLE" and not result["project"]["is_blocked"]
        cases.append({"name": "reversible ambiguity", "passed": ok})

        # 2. A missing local tool does not block an available substitute path.
        project = make(
            "missing-tool",
            [
                {"title": "Use missing tool", "success_criteria": ["output exists"], "value": 10},
                {"title": "Use local substitute", "success_criteria": ["output exists"], "value": 5},
            ],
        )
        first = project["actions"][0]
        store.claim_action(first["id"], {"worker_id": "w2", "claim_key": "tool-1"})
        result = store.record_result(
            first["id"],
            {
                "worker_id": "w2",
                "outcome": "blocked",
                "summary": "Preferred tool is not installed",
                "evidence": _evidence("missing-tool"),
                "obstacle_class": "LOCAL BLOCKER",
                "next_step": "Use local substitute",
            },
        )
        ok = result["project"]["blocker_truth"] == "none" and result["project"]["closure"]["next"] == "Use local substitute"
        cases.append({"name": "missing tool with substitute", "passed": ok})

        # 3. An irreversible effect pauses at an explicit approval gate.
        project = make(
            "late-approval",
            [{"title": "Publish", "success_criteria": ["release visible"], "risk": "irreversible"}],
        )
        action = project["actions"][0]
        store.claim_action(action["id"], {"worker_id": "w3", "claim_key": "approval-1"})
        denied = False
        try:
            store.reserve_effect(
                action["id"],
                {
                    "idempotency_key": "publish-once",
                    "effect_kind": "publish",
                    "target": "release://fixture",
                    "payload_digest": "a" * 64,
                },
            )
        except ClosureError as error:
            denied = error.code == "approval_required"
        store.claim_action(
            action["id"],
            {
                "worker_id": "w3",
                "claim_key": "approval-2",
                "approval_ref": "approval://fixture",
                "resolution_evidence": _evidence("approval"),
            },
        )
        reserved = store.reserve_effect(
            action["id"],
            {
                "idempotency_key": "publish-once",
                "effect_kind": "publish",
                "target": "release://fixture",
                "payload_digest": "a" * 64,
                "approval_ref": "approval://fixture",
            },
        )
        cases.append({"name": "late approval gate", "passed": denied and not reserved["idempotent"]})

        # 4. Interruption/resume and a duplicate effect preserve one attempt/effect.
        project = make(
            "resume-idempotently",
            [{"title": "Apply patch", "success_criteria": ["patch applied"]}],
        )
        action = project["actions"][0]
        first_claim = store.claim_action(action["id"], {"worker_id": "w4", "claim_key": "resume-1"})
        restarted = ClosureStore(db_path)
        second_claim = restarted.claim_action(action["id"], {"worker_id": "w4", "claim_key": "resume-1"})
        first_effect = restarted.reserve_effect(
            action["id"],
            {
                "idempotency_key": "patch-once",
                "effect_kind": "patch",
                "target": "workspace://fixture/file",
                "payload_digest": "b" * 64,
            },
        )
        second_effect = restarted.reserve_effect(
            action["id"],
            {
                "idempotency_key": "patch-once",
                "effect_kind": "patch",
                "target": "workspace://fixture/file",
                "payload_digest": "b" * 64,
            },
        )
        view = restarted.get_project(project["id"])
        ok = (
            not first_claim["idempotent"]
            and second_claim["idempotent"]
            and not first_effect["idempotent"]
            and second_effect["idempotent"]
            and view["budget"]["attempts"] == 1
            and view["budget"]["effects"] == 1
        )
        cases.append({"name": "interruption and duplicate effect", "passed": ok})

        # 5. A worker's unsupported success cannot pass without a distinct verifier.
        project = make(
            "lying-worker",
            [{"title": "Prove completion", "success_criteria": ["test passes"], "max_attempts": 2}],
        )
        action = project["actions"][0]
        store.claim_action(action["id"], {"worker_id": "w5", "claim_key": "liar-1"})
        store.record_result(
            action["id"],
            {
                "worker_id": "w5",
                "outcome": "success",
                "summary": "Trust me",
                "evidence": _evidence("worker-claim"),
            },
        )
        self_verified = False
        try:
            store.verify_action(
                action["id"],
                {
                    "verifier_id": "w5",
                    "verdict": "pass",
                    "summary": "Self approval",
                    "evidence": _evidence("self-approval"),
                },
            )
        except ClosureError as error:
            self_verified = error.code == "independence_required"
        failed = store.verify_action(
            action["id"],
            {
                "verifier_id": "v5",
                "verdict": "fail",
                "summary": "Evidence does not satisfy the criterion",
                "evidence": _evidence("verifier-failure"),
            },
        )
        contained = self_verified and failed["project"]["status"] != "PASSED"
        store.claim_action(
            action["id"], {"worker_id": "w5", "claim_key": "liar-2"}
        )
        store.record_result(
            action["id"],
            {
                "worker_id": "w5",
                "outcome": "success",
                "summary": "The acceptance test now passes",
                "evidence": _evidence("corrected-worker-result"),
            },
        )
        closed = store.verify_action(
            action["id"],
            {
                "verifier_id": "v5",
                "verdict": "pass",
                "summary": "Acceptance test independently reproduced",
                "evidence": _evidence("independent-pass"),
            },
        )
        ok = contained and closed["project"]["status"] == "PASSED"
        cases.append({"name": "lying worker containment", "passed": ok})

        health = store.health()
    passed = sum(case["passed"] for case in cases)
    return {
        "status": "pass" if passed == len(cases) else "fail",
        "passed": passed,
        "required": len(cases),
        "cases": cases,
        "containment_rate": 1.0 if passed == len(cases) else passed / len(cases),
        "health": health,
        "conformance": "UNSCORED",
    }


def run_five_fixture_self_test() -> dict[str, Any]:
    """Close five adversarial projects through the same durable control paths."""
    cases: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="closure-self-test-") as directory:
        db_path = Path(directory) / "closure.sqlite3"
        store = ClosureStore(db_path)

        def make(name: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
            return store.create_project(
                {
                    "name": name,
                    "objective": f"Close {name}",
                    "closed_when": ["Every action has independent passing evidence"],
                    "proposer_id": "fixture-proposer",
                    "ratifier_id": "fixture-ratifier",
                    "actions": actions,
                    "max_attempts": 12,
                    "max_effects": 5,
                }
            )

        def finish(
            action_id: str, worker: str, verifier: str, attempt_key: str, label: str
        ) -> dict[str, Any]:
            result = store.record_result(
                action_id,
                {
                    "worker_id": worker,
                    "attempt_key": attempt_key,
                    "outcome": "success",
                    "summary": f"{label} evidence produced",
                    "evidence": _evidence(f"{label}-worker"),
                },
            )
            return store.verify_action(
                action_id,
                {
                    "verifier_id": verifier,
                    "attempt_key": attempt_key,
                    "result_receipt_id": result["receipt"]["id"],
                    "verdict": "pass",
                    "summary": f"{label} independently reproduced",
                    "evidence": _evidence(f"{label}-verifier"),
                },
            )["project"]

        def complete_effect(key: str, adapter: str, label: str) -> None:
            dispatched = store.mark_effect(
                key, {"adapter_id": adapter, "status": "DISPATCHED"}
            )
            store.mark_effect(
                key,
                {
                    "adapter_id": adapter,
                    "status": "COMPLETED",
                    "dispatch_token": dispatched["reservation"]["dispatch_token"],
                    "result_digest": hashlib.sha256(f"{label}-result".encode()).hexdigest(),
                    "evidence": _evidence(f"{label}-effect"),
                },
            )

        # 1: reversible ambiguity is contained, retried, verified, and closed.
        project = make(
            "reversible-ambiguity",
            [{"title": "Resolve ambiguity", "success_criteria": ["decision tested"]}],
        )
        action = project["actions"][0]
        store.claim_action(action["id"], {"worker_id": "w1", "claim_key": "ambiguity-1"})
        result = store.record_result(
            action["id"],
            {
                "worker_id": "w1",
                "attempt_key": "ambiguity-1",
                "outcome": "blocked",
                "summary": "Several reversible choices remain",
                "evidence": _evidence("ambiguity"),
                "obstacle_class": "UNCERTAINTY",
                "next_step": "Try the cheapest reversible choice",
            },
        )
        contained = result["project"]["actions"][0]["status"] == "FAILED_RETRYABLE" and not result["project"]["is_blocked"]
        store.claim_action(action["id"], {"worker_id": "w1", "claim_key": "ambiguity-2"})
        closed = finish(action["id"], "w1", "v1", "ambiguity-2", "ambiguity")
        cases.append({"name": "reversible ambiguity", "passed": contained and closed["status"] == "PASSED", "closed": closed["status"] == "PASSED"})

        # 2: a missing tool leaves a substitute runnable and the project closes.
        project = make(
            "missing-tool",
            [
                {"title": "Use missing tool", "success_criteria": ["output exists"], "value": 10},
                {"title": "Use local substitute", "success_criteria": ["output exists"], "value": 5},
            ],
        )
        preferred, substitute = project["actions"]
        store.claim_action(preferred["id"], {"worker_id": "w2", "claim_key": "tool-1"})
        result = store.record_result(
            preferred["id"],
            {
                "worker_id": "w2",
                "attempt_key": "tool-1",
                "outcome": "blocked",
                "summary": "Preferred tool is not installed",
                "evidence": _evidence("missing-tool"),
                "obstacle_class": "LOCAL BLOCKER",
                "next_step": "Use local substitute",
            },
        )
        contained = result["project"]["blocker_truth"] == "none" and result["project"]["closure"]["next"] == "Use local substitute"
        store.claim_action(substitute["id"], {"worker_id": "w2s", "claim_key": "substitute-1"})
        finish(substitute["id"], "w2s", "v2s", "substitute-1", "substitute")
        store.claim_action(
            preferred["id"],
            {
                "worker_id": "w2",
                "claim_key": "tool-1",
                "resolution_evidence": _evidence("substitute-satisfies-output"),
            },
        )
        closed = finish(preferred["id"], "w2", "v2", "tool-1", "preferred-via-substitute")
        cases.append({"name": "missing tool with substitute", "passed": contained and closed["status"] == "PASSED", "closed": closed["status"] == "PASSED"})

        # 3: late approval resumes the same attempt, reconciles, and closes.
        project = make(
            "late-approval",
            [{"title": "Publish", "success_criteria": ["release visible"], "risk": "irreversible"}],
        )
        action = project["actions"][0]
        store.claim_action(action["id"], {"worker_id": "w3", "claim_key": "approval-1"})
        effect = {
            "idempotency_key": "publish-once",
            "attempt_key": "approval-1",
            "effect_kind": "publish",
            "target": "release://fixture",
            "payload_digest": "a" * 64,
        }
        denied = False
        try:
            store.reserve_effect(action["id"], effect)
        except ClosureError as error:
            denied = error.code == "approval_required"
        resumed = store.claim_action(
            action["id"],
            {
                "worker_id": "w3",
                "claim_key": "approval-1",
                "approval_ref": "approval://fixture",
                "resolution_evidence": _evidence("approval"),
            },
        )
        store.reserve_effect(action["id"], {**effect, "approval_ref": "approval://fixture"})
        complete_effect("publish-once", "adapter-3", "publish")
        closed = finish(action["id"], "w3", "v3", "approval-1", "publish")
        cases.append({"name": "late approval gate", "passed": denied and resumed["resumed"] and closed["status"] == "PASSED", "closed": closed["status"] == "PASSED"})

        # 4: restart and duplicate replay spend once, then close.
        project = make(
            "resume-idempotently",
            [{"title": "Apply patch", "success_criteria": ["patch applied"]}],
        )
        action = project["actions"][0]
        first_claim = store.claim_action(action["id"], {"worker_id": "w4", "claim_key": "resume-1"})
        store = ClosureStore(db_path)
        second_claim = store.claim_action(action["id"], {"worker_id": "w4", "claim_key": "resume-1"})
        patch_effect = {
            "idempotency_key": "patch-once",
            "attempt_key": "resume-1",
            "effect_kind": "patch",
            "target": "workspace://fixture/file",
            "payload_digest": "b" * 64,
        }
        first_effect = store.reserve_effect(action["id"], patch_effect)
        second_effect = store.reserve_effect(action["id"], patch_effect)
        before = store.get_project(project["id"])
        contained = (
            not first_claim["idempotent"]
            and second_claim["idempotent"]
            and not first_effect["idempotent"]
            and second_effect["idempotent"]
            and before["budget"]["attempts"] == 1
            and before["budget"]["effects"] == 1
        )
        complete_effect("patch-once", "adapter-4", "patch")
        closed = finish(action["id"], "w4", "v4", "resume-1", "patch")
        cases.append({"name": "interruption and duplicate effect", "passed": contained and closed["status"] == "PASSED", "closed": closed["status"] == "PASSED"})

        # 5: a lying worker is contained, corrects the work, and closes.
        project = make(
            "lying-worker",
            [{"title": "Prove completion", "success_criteria": ["test passes"], "max_attempts": 2}],
        )
        action = project["actions"][0]
        store.claim_action(action["id"], {"worker_id": "w5", "claim_key": "liar-1"})
        first_result = store.record_result(
            action["id"],
            {"worker_id": "w5", "attempt_key": "liar-1", "outcome": "success", "summary": "Trust me", "evidence": _evidence("worker-claim")},
        )
        self_verified = False
        try:
            store.verify_action(
                action["id"],
                {"verifier_id": "w5", "attempt_key": "liar-1", "result_receipt_id": first_result["receipt"]["id"], "verdict": "pass", "summary": "Self approval", "evidence": _evidence("self-approval")},
            )
        except ClosureError as error:
            self_verified = error.code == "independence_required"
        failed = store.verify_action(
            action["id"],
            {"verifier_id": "v5", "attempt_key": "liar-1", "result_receipt_id": first_result["receipt"]["id"], "verdict": "fail", "summary": "Evidence does not satisfy the criterion", "evidence": _evidence("verifier-failure")},
        )
        contained = self_verified and failed["project"]["status"] != "PASSED"
        store.claim_action(action["id"], {"worker_id": "w5", "claim_key": "liar-2"})
        closed = finish(action["id"], "w5", "v5", "liar-2", "corrected-worker-result")
        cases.append({"name": "lying worker containment", "passed": contained and closed["status"] == "PASSED", "closed": closed["status"] == "PASSED"})

        health = store.health()
    passed = sum(case["passed"] for case in cases)
    closures = sum(case["closed"] for case in cases)
    return {
        "status": "pass" if passed == 5 and closures == 5 else "fail",
        "passed": passed,
        "required": 5,
        "closures": closures,
        "cases": cases,
        "containment_rate": passed / 5,
        "duplicate_effects": 0 if passed == 5 else None,
        "operator_rescues": 0,
        "health": health,
        "conformance": "UNSCORED",
    }


closure_router = APIRouter(prefix="/api/closure", tags=["Closure Control"])
_store: ClosureStore | None = None


def get_closure_store() -> ClosureStore:
    global _store
    if _store is None:
        path = os.environ.get(
            "FLOYD_CLOSURE_DB",
            str(Path(__file__).resolve().parent / ".floyd" / "closure-control.sqlite3"),
        )
        _store = ClosureStore(path)
    return _store


def _translate(error: ClosureError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    )


def _require_mutation_authority(request: Request, provided_token: str | None) -> None:
    """Keep the zero-config API local; require a bearer-equivalent token remotely."""
    expected = os.environ.get("FLOYD_CLOSURE_TOKEN")
    if expected:
        if provided_token and hmac.compare_digest(provided_token, expected):
            return
        raise HTTPException(
            status_code=403,
            detail={"code": "mutation_authority_required", "message": "Invalid closure token"},
        )
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    if host in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "local_authority_required",
            "message": "Remote closure mutations require FLOYD_CLOSURE_TOKEN",
        },
    )


def _scoped_principals() -> dict[str, dict[str, Any]] | None:
    raw = os.environ.get("FLOYD_CLOSURE_PRINCIPALS_JSON")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=503,
            detail={"code": "invalid_authority_config", "message": "Invalid principal JSON"},
        ) from error
    if not isinstance(parsed, dict) or not parsed:
        raise HTTPException(
            status_code=503,
            detail={"code": "invalid_authority_config", "message": "Principal registry is empty"},
        )
    normalized: dict[str, dict[str, Any]] = {}
    seen_tokens: set[str] = set()
    for principal_id, entry in parsed.items():
        if (
            not isinstance(principal_id, str)
            or not isinstance(entry, dict)
            or not isinstance(entry.get("token"), str)
            or not entry["token"]
            or not isinstance(entry.get("roles"), list)
            or not entry["roles"]
            or not all(isinstance(role, str) and role for role in entry["roles"])
        ):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "invalid_authority_config",
                    "message": "Each principal needs a non-empty token and role list",
                },
            )
        if entry["token"] in seen_tokens:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "invalid_authority_config",
                    "message": "Principal tokens must be unique",
                },
            )
        seen_tokens.add(entry["token"])
        normalized[principal_id] = {
            "token": entry["token"],
            "roles": frozenset(entry["roles"]),
        }
    return normalized


def _require_scoped_authority(
    role: str, claimed_principal: str | None, provided_token: str | None
) -> None:
    principals = _scoped_principals()
    if principals is None:
        if os.environ.get("FLOYD_CLOSURE_TOKEN"):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "scoped_authority_unconfigured",
                    "message": "Remote mode requires FLOYD_CLOSURE_PRINCIPALS_JSON",
                },
            )
        return
    entry = principals.get(claimed_principal or "")
    if (
        entry is None
        or role not in entry["roles"]
        or not provided_token
        or not hmac.compare_digest(provided_token, entry["token"])
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "scoped_authority_required",
                "message": f"A credential bound to the claimed {role} principal is required",
            },
        )


def _require_approval_authority(approval_ref: str | None, provided_token: str | None) -> None:
    if not approval_ref:
        return
    expected = os.environ.get("FLOYD_CLOSURE_APPROVAL_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "approval_authority_unconfigured",
                "message": "Approval-bearing API calls require FLOYD_CLOSURE_APPROVAL_TOKEN",
            },
        )
    principals = _scoped_principals() or {}
    reserved_tokens = {entry["token"] for entry in principals.values()}
    mutation_token = os.environ.get("FLOYD_CLOSURE_TOKEN")
    if expected == mutation_token or expected in reserved_tokens:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "invalid_authority_config",
                "message": "Approval authority must use a distinct credential",
            },
        )
    if not provided_token or not hmac.compare_digest(provided_token, expected):
        raise HTTPException(
            status_code=403,
            detail={"code": "approval_authority_required", "message": "Invalid approval token"},
        )


def _authority_health(open_approval_actions: int) -> dict[str, Any]:
    remote = bool(os.environ.get("FLOYD_CLOSURE_TOKEN"))
    registry_present = bool(os.environ.get("FLOYD_CLOSURE_PRINCIPALS_JSON"))
    try:
        principals = _scoped_principals()
    except HTTPException:
        return {
            "ok": False,
            "mode": "invalid",
            "scoped_role_credentials": False,
            "reason": "invalid_principal_registry",
        }
    if remote and principals is None:
        return {
            "ok": False,
            "mode": "remote_incomplete",
            "scoped_role_credentials": False,
            "reason": "remote_mode_requires_principal_registry",
        }
    missing_roles: list[str] = []
    separation_failures: list[str] = []
    if principals:
        required_roles = {"proposer", "ratifier", "worker", "verifier", "adapter", "operator"}
        role_principals = {
            role: {
                principal_id
                for principal_id, entry in principals.items()
                if role in entry["roles"]
            }
            for role in required_roles
        }
        missing_roles = sorted(role for role, ids in role_principals.items() if not ids)
        if not any(
            proposer != ratifier
            for proposer in role_principals["proposer"]
            for ratifier in role_principals["ratifier"]
        ):
            separation_failures.append("proposer_ratifier")
        if not any(
            worker != verifier
            for worker in role_principals["worker"]
            for verifier in role_principals["verifier"]
        ):
            separation_failures.append("worker_verifier")
        if missing_roles:
            return {
                "ok": False,
                "mode": "scoped_credentials",
                "scoped_role_credentials": True,
                "reason": "missing_required_roles",
                "missing_roles": missing_roles,
                "separation_failures": separation_failures,
            }
        if separation_failures:
            return {
                "ok": False,
                "mode": "scoped_credentials",
                "scoped_role_credentials": True,
                "reason": "insufficient_role_separation",
                "missing_roles": missing_roles,
                "separation_failures": separation_failures,
            }
    approval_token = os.environ.get("FLOYD_CLOSURE_APPROVAL_TOKEN")
    reserved_tokens = {entry["token"] for entry in (principals or {}).values()}
    mutation_token = os.environ.get("FLOYD_CLOSURE_TOKEN")
    if approval_token and (approval_token == mutation_token or approval_token in reserved_tokens):
        return {
            "ok": False,
            "mode": "invalid",
            "scoped_role_credentials": bool(principals),
            "reason": "approval_credential_not_distinct",
        }
    if open_approval_actions and not approval_token:
        return {
            "ok": False,
            "mode": "scoped_credentials" if principals else "loopback_trust",
            "scoped_role_credentials": bool(principals),
            "reason": "open_approval_actions_require_approval_credential",
        }
    return {
        "ok": True,
        "mode": "scoped_credentials" if principals else "loopback_trust",
        "scoped_role_credentials": bool(principals),
        "reason": None,
        "registry_present": registry_present,
        "missing_roles": missing_roles,
        "separation_failures": separation_failures,
    }


@closure_router.get("/board")
def closure_board():
    return get_closure_store().board()


@closure_router.post("/projects", status_code=201)
def closure_create_project(
    spec: ProjectCreate,
    http_request: Request,
    x_floyd_closure_token: str | None = Header(default=None),
    x_floyd_closure_proposer_token: str | None = Header(default=None),
    x_floyd_closure_ratifier_token: str | None = Header(default=None),
):
    _require_mutation_authority(http_request, x_floyd_closure_token)
    _require_scoped_authority(
        "proposer", spec.proposer_id, x_floyd_closure_proposer_token
    )
    _require_scoped_authority(
        "ratifier", spec.ratifier_id, x_floyd_closure_ratifier_token
    )
    try:
        return get_closure_store().create_project(spec)
    except ClosureError as error:
        raise _translate(error) from error


@closure_router.get("/projects/{project_id}")
def closure_get_project(project_id: str):
    try:
        return get_closure_store().get_project(project_id)
    except ClosureError as error:
        raise _translate(error) from error


@closure_router.post("/actions/{action_id}/claim")
def closure_claim_action(
    action_id: str,
    request: ClaimRequest,
    http_request: Request,
    x_floyd_closure_token: str | None = Header(default=None),
    x_floyd_closure_principal_token: str | None = Header(default=None),
    x_floyd_closure_approval_token: str | None = Header(default=None),
):
    _require_mutation_authority(http_request, x_floyd_closure_token)
    _require_scoped_authority(
        "worker", request.worker_id, x_floyd_closure_principal_token
    )
    _require_approval_authority(request.approval_ref, x_floyd_closure_approval_token)
    try:
        return get_closure_store().claim_action(action_id, request)
    except ClosureError as error:
        raise _translate(error) from error


@closure_router.post("/actions/{action_id}/result")
def closure_record_result(
    action_id: str,
    request: ResultRequest,
    http_request: Request,
    x_floyd_closure_token: str | None = Header(default=None),
    x_floyd_closure_principal_token: str | None = Header(default=None),
):
    _require_mutation_authority(http_request, x_floyd_closure_token)
    _require_scoped_authority(
        "worker", request.worker_id, x_floyd_closure_principal_token
    )
    try:
        return get_closure_store().record_result(action_id, request)
    except ClosureError as error:
        raise _translate(error) from error


@closure_router.post("/actions/{action_id}/verify")
def closure_verify_action(
    action_id: str,
    request: VerifyRequest,
    http_request: Request,
    x_floyd_closure_token: str | None = Header(default=None),
    x_floyd_closure_principal_token: str | None = Header(default=None),
):
    _require_mutation_authority(http_request, x_floyd_closure_token)
    _require_scoped_authority(
        "verifier", request.verifier_id, x_floyd_closure_principal_token
    )
    try:
        return get_closure_store().verify_action(action_id, request)
    except ClosureError as error:
        raise _translate(error) from error


@closure_router.post("/actions/{action_id}/effects/reserve")
def closure_reserve_effect(
    action_id: str,
    request: EffectRequest,
    http_request: Request,
    x_floyd_closure_token: str | None = Header(default=None),
    x_floyd_closure_principal_token: str | None = Header(default=None),
    x_floyd_closure_approval_token: str | None = Header(default=None),
):
    _require_mutation_authority(http_request, x_floyd_closure_token)
    _require_approval_authority(request.approval_ref, x_floyd_closure_approval_token)
    try:
        store = get_closure_store()
        actor = store.action_authority_context(action_id)
        _require_scoped_authority(
            "worker", actor["worker_id"], x_floyd_closure_principal_token
        )
        return store.reserve_effect(action_id, request)
    except ClosureError as error:
        raise _translate(error) from error


@closure_router.post("/effects/{idempotency_key}/status")
def closure_mark_effect(
    idempotency_key: str,
    request: EffectStatusRequest,
    http_request: Request,
    x_floyd_closure_token: str | None = Header(default=None),
    x_floyd_closure_principal_token: str | None = Header(default=None),
):
    _require_mutation_authority(http_request, x_floyd_closure_token)
    _require_scoped_authority(
        "adapter", request.adapter_id, x_floyd_closure_principal_token
    )
    try:
        return get_closure_store().mark_effect(idempotency_key, request)
    except ClosureError as error:
        raise _translate(error) from error


@closure_router.post("/effects/{idempotency_key}/reconcile")
def closure_reconcile_effect(
    idempotency_key: str,
    request: EffectReconcileRequest,
    http_request: Request,
    x_floyd_closure_token: str | None = Header(default=None),
    x_floyd_closure_principal_token: str | None = Header(default=None),
    x_floyd_closure_approval_token: str | None = Header(default=None),
):
    _require_mutation_authority(http_request, x_floyd_closure_token)
    _require_scoped_authority(
        "operator", request.operator_id, x_floyd_closure_principal_token
    )
    _require_approval_authority(
        request.authorization_ref, x_floyd_closure_approval_token
    )
    try:
        return get_closure_store().reconcile_unknown_effect(idempotency_key, request)
    except ClosureError as error:
        raise _translate(error) from error


@closure_router.post("/projects/{project_id}/halt")
def closure_halt_project(
    project_id: str,
    request: HaltRequest,
    http_request: Request,
    x_floyd_closure_token: str | None = Header(default=None),
    x_floyd_closure_principal_token: str | None = Header(default=None),
):
    _require_mutation_authority(http_request, x_floyd_closure_token)
    _require_scoped_authority(
        "operator", request.operator_id, x_floyd_closure_principal_token
    )
    try:
        return get_closure_store().halt_project(project_id, request)
    except ClosureError as error:
        raise _translate(error) from error


@closure_router.get("/projects/{project_id}/receipts")
def closure_receipts(project_id: str, limit: int = 100):
    try:
        return {"receipts": get_closure_store().receipts(project_id, limit)}
    except ClosureError as error:
        raise _translate(error) from error


@closure_router.get("/health")
def closure_health():
    return get_closure_store().health()


@closure_router.post("/self-test")
def closure_self_test():
    return run_five_fixture_self_test()
