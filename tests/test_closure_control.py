"""Dependency-light tests for the deterministic closure control plane.

These are unittest cases so the core protocol remains verifiable even before
the optional FastAPI/pytest development environment is installed.  Pytest
also discovers and runs them in the normal project gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from closure_control import (
    ClosureError,
    ClosureStore,
    HTTPException,
    _require_approval_authority,
    _require_scoped_authority,
    run_five_fixture_self_test,
)


def evidence(label: str) -> list[dict[str, str]]:
    return [
        {
            "source": f"test://{label}",
            "digest": hashlib.sha256(label.encode()).hexdigest(),
            "summary": label,
        }
    ]


class ClosureControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="closure-test-")
        self.path = Path(self.tempdir.name) / "closure.sqlite3"
        self.store = ClosureStore(self.path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_project(self, actions: list[dict] | None = None, **overrides):
        payload = {
            "name": "Ship the slice",
            "objective": "Close one independently verifiable vertical slice",
            "closed_when": ["All specified acceptance tests pass"],
            "proposer_id": "planner",
            "ratifier_id": "ratifier",
            "actions": actions
            or [{"title": "Implement slice", "success_criteria": ["test passes"]}],
            "max_attempts": 8,
            "max_effects": 4,
        }
        payload.update(overrides)
        return self.store.create_project(payload)

    def test_five_adversarial_runs_pass(self):
        result = run_five_fixture_self_test()
        self.assertEqual(result["status"], "pass")
        self.assertEqual((result["passed"], result["required"]), (5, 5))
        self.assertEqual(result["closures"], 5)
        self.assertEqual(result["containment_rate"], 1.0)

    def test_contract_requires_a_distinct_ratifier(self):
        with self.assertRaises(ValueError):
            self.create_project(proposer_id="same", ratifier_id="same")

    def test_remote_roles_bind_credentials_to_claimed_principals(self):
        principals = json.dumps(
            {
                "planner": {"token": "proposer-credential", "roles": ["proposer"]},
                "ratifier": {"token": "ratifier-credential", "roles": ["ratifier"]},
                "worker": {"token": "worker-credential", "roles": ["worker"]},
                "verifier": {"token": "verifier-credential", "roles": ["verifier"]},
            }
        )
        with patch.dict(
            os.environ,
            {
                "FLOYD_CLOSURE_TOKEN": "perimeter-credential",
                "FLOYD_CLOSURE_PRINCIPALS_JSON": principals,
                "FLOYD_CLOSURE_APPROVAL_TOKEN": "approval-credential",
            },
            clear=True,
        ):
            _require_scoped_authority("worker", "worker", "worker-credential")
            _require_approval_authority("approval://fixture", "approval-credential")
            with self.assertRaises(HTTPException):
                _require_scoped_authority("verifier", "worker", "worker-credential")
            with self.assertRaises(HTTPException):
                _require_scoped_authority("worker", "worker", "verifier-credential")

    def test_remote_role_tokens_must_be_unique(self):
        principals = json.dumps(
            {
                "worker": {"token": "shared", "roles": ["worker"]},
                "verifier": {"token": "shared", "roles": ["verifier"]},
            }
        )
        with patch.dict(
            os.environ,
            {
                "FLOYD_CLOSURE_TOKEN": "perimeter",
                "FLOYD_CLOSURE_PRINCIPALS_JSON": principals,
            },
            clear=True,
        ), self.assertRaises(HTTPException):
            _require_scoped_authority("worker", "worker", "shared")

    def test_contract_budget_allows_one_attempt_per_required_action(self):
        with self.assertRaisesRegex(ValueError, "one attempt per required action"):
            self.create_project(
                actions=[
                    {"title": "First", "success_criteria": ["first passes"]},
                    {"title": "Second", "success_criteria": ["second passes"]},
                ],
                max_attempts=1,
            )

    def test_contract_effect_budget_covers_approval_required_actions(self):
        with self.assertRaisesRegex(ValueError, "one effect per approval-required action"):
            self.create_project(
                actions=[
                    {
                        "title": "Publish first",
                        "success_criteria": ["first release is visible"],
                        "risk": "irreversible",
                    },
                    {
                        "title": "Publish second",
                        "success_criteria": ["second release is visible"],
                        "risk": "irreversible",
                    },
                ],
                max_effects=1,
            )

    def test_read_only_action_cannot_require_external_effect_approval(self):
        with self.assertRaisesRegex(
            ValueError, "read_only actions cannot require an external-effect approval"
        ):
            self.create_project(
                actions=[
                    {
                        "title": "Inspect",
                        "success_criteria": ["inspection is complete"],
                        "risk": "read_only",
                        "requires_approval": True,
                    }
                ]
            )

    def test_non_finite_leverage_inputs_are_rejected(self):
        for field, value in (
            ("value", float("inf")),
            ("probability", float("nan")),
            ("remaining_effort", float("inf")),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.create_project(
                    actions=[
                        {
                            "title": "Finite action",
                            "success_criteria": ["finite"],
                            field: value,
                        }
                    ]
                )

    def test_effect_result_digest_must_be_sha256_hex(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "digest-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "digest-effect",
                "attempt_key": "digest-1",
                "effect_kind": "patch",
                "target": "workspace://fixture/digest",
                "payload_digest": "a" * 64,
            },
        )
        dispatched = self.store.mark_effect(
            "digest-effect", {"adapter_id": "adapter", "status": "DISPATCHED"}
        )
        with self.assertRaises(ValueError):
            self.store.mark_effect(
                "digest-effect",
                {
                    "adapter_id": "adapter",
                    "status": "COMPLETED",
                    "result_digest": "not-a-sha256-digest",
                    "dispatch_token": dispatched["reservation"]["dispatch_token"],
                    "evidence": evidence("bad-digest"),
                },
            )

    def test_future_schema_version_fails_closed(self):
        self.create_project()
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE closure_schema_meta SET version = 99 WHERE singleton = 1")
        with self.assertRaisesRegex(ClosureError, "newer than this runtime"):
            ClosureStore(self.path)
        with sqlite3.connect(self.path) as conn:
            version = conn.execute(
                "SELECT version FROM closure_schema_meta WHERE singleton = 1"
            ).fetchone()[0]
        self.assertEqual(version, 99)

    def test_project_creation_request_key_is_idempotent(self):
        first = self.create_project(request_key="create-once")
        replay = self.create_project(request_key="create-once")
        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(self.store.board()["metrics"]["total"], 1)

    def test_project_creation_key_rejects_changed_payload(self):
        self.create_project(request_key="create-once")
        with self.assertRaisesRegex(ClosureError, "different project contract"):
            self.create_project(request_key="create-once", objective="A different objective")

    def test_receipt_binding_repairs_a_deleted_project_request_without_duplication(self):
        first = self.create_project(request_key="durable-create-once")
        with sqlite3.connect(self.path) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM project_requests WHERE request_key = 'durable-create-once'"
            )
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TRIGGER project_requests_no_delete")
            conn.execute(
                "DELETE FROM project_requests WHERE request_key = 'durable-create-once'"
            )
        materialization = self.store.health()["layers"]["control"]["materialization"]
        self.assertGreater(materialization["project_request_binding_mismatches"], 0)
        replay = self.create_project(request_key="durable-create-once")
        self.assertEqual(replay["id"], first["id"])
        self.assertEqual(self.store.board()["metrics"]["total"], 1)
        self.assertEqual(
            self.store.health()["layers"]["control"]["materialization"][
                "project_request_binding_mismatches"
            ],
            0,
        )

    def test_worker_claim_cannot_close_without_distinct_verifier(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "claim-1"})
        worker_result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "claim-1",
                "outcome": "success",
                "summary": "Implemented",
                "evidence": evidence("worker-result"),
            },
        )
        with self.assertRaisesRegex(ClosureError, "cannot verify"):
            self.store.verify_action(
                action_id,
                {
                    "verifier_id": "worker",
                    "attempt_key": "claim-1",
                    "result_receipt_id": worker_result["receipt"]["id"],
                    "verdict": "pass",
                    "summary": "Looks good",
                    "evidence": evidence("self-verification"),
                },
            )
        result = self.store.verify_action(
            action_id,
            {
                "verifier_id": "independent-verifier",
                "attempt_key": "claim-1",
                "result_receipt_id": worker_result["receipt"]["id"],
                "verdict": "pass",
                "summary": "Reproduced acceptance result",
                "evidence": evidence("independent-verification"),
            },
        )
        self.assertEqual(result["project"]["status"], "PASSED")

    def test_worker_result_and_verdict_replays_return_original_receipts(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "replay-1"})
        result_request = {
            "worker_id": "worker",
            "attempt_key": "replay-1",
            "outcome": "success",
            "summary": "Completed once",
            "evidence": evidence("replay-result"),
        }
        first_result = self.store.record_result(action_id, result_request)
        replayed_result = self.store.record_result(action_id, result_request)
        self.assertTrue(replayed_result["idempotent"])
        self.assertEqual(replayed_result["receipt"]["id"], first_result["receipt"]["id"])
        with self.assertRaisesRegex(ClosureError, "different worker result"):
            self.store.record_result(
                action_id, {**result_request, "summary": "Conflicting second result"}
            )

        verdict_request = {
            "verifier_id": "verifier",
            "attempt_key": "replay-1",
            "result_receipt_id": first_result["receipt"]["id"],
            "verdict": "pass",
            "summary": "Verified once",
            "evidence": evidence("replay-verdict"),
        }
        first_verdict = self.store.verify_action(action_id, verdict_request)
        replayed_verdict = self.store.verify_action(action_id, verdict_request)
        self.assertTrue(replayed_verdict["idempotent"])
        self.assertEqual(replayed_verdict["receipt"]["id"], first_verdict["receipt"]["id"])
        with self.assertRaisesRegex(ClosureError, "different verifier verdict"):
            self.store.verify_action(
                action_id, {**verdict_request, "summary": "Conflicting second verdict"}
            )

    def test_hard_blocker_requires_supported_reason(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "claim-1"})
        with self.assertRaisesRegex(ClosureError, "Hard blockers require"):
            self.store.record_result(
                action_id,
                {
                    "worker_id": "worker",
                    "attempt_key": "claim-1",
                    "outcome": "blocked",
                    "summary": "I do not know what to do",
                    "evidence": evidence("unsupported-blocker"),
                    "obstacle_class": "HARD BLOCKER",
                    "hard_reason": "uncertainty",
                },
            )
        self.assertEqual(self.store.get_project(project["id"])["status"], "RUNNING")

    def test_worker_cannot_self_declare_an_approval_gate(self):
        project = self.create_project(
            actions=[
                {"title": "Inspect", "success_criteria": ["inspected"], "risk": "read_only"}
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "inspect-1"})
        with self.assertRaisesRegex(ClosureError, "emitted only by the effect approval policy"):
            self.store.record_result(
                action_id,
                {
                    "worker_id": "worker",
                    "attempt_key": "inspect-1",
                    "outcome": "blocked",
                    "summary": "Worker claims approval is needed",
                    "evidence": evidence("worker-approval-claim"),
                    "obstacle_class": "APPROVAL GATE",
                },
            )
        view = self.store.get_project(project["id"])
        self.assertEqual(view["status"], "RUNNING")
        self.assertEqual(view["blocker_truth"], "none")

    def test_final_attempt_uncertainty_is_not_advertised_as_retryable(self):
        project = self.create_project(
            actions=[
                {
                    "title": "Bounded investigation",
                    "success_criteria": ["answer is certain"],
                    "max_attempts": 1,
                }
            ],
            max_attempts=2,
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "uncertain-1"})
        result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "uncertain-1",
                "outcome": "blocked",
                "summary": "The bounded investigation remains uncertain",
                "evidence": evidence("final-uncertainty"),
                "obstacle_class": "UNCERTAINTY",
                "next_step": "Ratify a different action",
            },
        )
        self.assertEqual(result["project"]["actions"][0]["status"], "QUARANTINED")
        self.assertEqual(result["project"]["status"], "QUARANTINED")
        self.assertIsNone(result["project"]["closure"]["next"])

    def test_project_is_blocked_only_when_no_runnable_path_remains(self):
        project = self.create_project(
            actions=[
                {
                    "title": "Obtain authority",
                    "success_criteria": ["authority supplied"],
                    "value": 10,
                },
                {
                    "title": "Finish independent local work",
                    "success_criteria": ["local output exists"],
                    "value": 5,
                },
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "claim-1"})
        result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "claim-1",
                "outcome": "blocked",
                "summary": "Only the owner can grant this authority",
                "evidence": evidence("authority-gate"),
                "obstacle_class": "HARD BLOCKER",
                "hard_reason": "unavailable_authority",
                "next_step": "Continue the independent local action",
            },
        )
        self.assertFalse(result["project"]["is_blocked"])
        self.assertEqual(result["project"]["blocker_truth"], "none")
        self.assertEqual(result["project"]["closure"]["next"], "Finish independent local work")

    def test_mixed_hard_and_local_dead_ends_are_not_whole_project_hard_blocker(self):
        project = self.create_project(
            actions=[
                {"title": "Owner authority", "success_criteria": ["authority exists"], "value": 10},
                {"title": "Local tool", "success_criteria": ["tool output exists"], "value": 5},
            ]
        )
        hard_id = project["actions"][0]["id"]
        local_id = project["actions"][1]["id"]
        self.store.claim_action(hard_id, {"worker_id": "w1", "claim_key": "hard-1"})
        self.store.record_result(
            hard_id,
            {
                "worker_id": "w1",
                "attempt_key": "hard-1",
                "outcome": "blocked",
                "summary": "Owner authority is unavailable",
                "evidence": evidence("hard"),
                "obstacle_class": "HARD BLOCKER",
                "hard_reason": "unavailable_authority",
            },
        )
        self.store.claim_action(local_id, {"worker_id": "w2", "claim_key": "local-1"})
        result = self.store.record_result(
            local_id,
            {
                "worker_id": "w2",
                "attempt_key": "local-1",
                "outcome": "blocked",
                "summary": "Local preferred tool is missing",
                "evidence": evidence("local"),
                "obstacle_class": "LOCAL BLOCKER",
            },
        )
        self.assertFalse(result["project"]["is_blocked"])
        self.assertEqual(result["project"]["blocker_truth"], "apparent")
        self.assertEqual(result["project"]["status"], "LOCAL_BLOCKED")

    def test_single_evidence_backed_hard_blocker_blocks_project(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "claim-1"})
        result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "claim-1",
                "outcome": "blocked",
                "summary": "A protected authority is unavailable",
                "evidence": evidence("protected-authority"),
                "obstacle_class": "HARD BLOCKER",
                "hard_reason": "unavailable_authority",
            },
        )
        self.assertTrue(result["project"]["is_blocked"])
        self.assertEqual(result["project"]["status"], "APPROVAL_PENDING")

    def test_terminal_quarantine_does_not_hide_the_only_open_hard_blocker(self):
        project = self.create_project(
            actions=[
                {
                    "title": "Exhausted path",
                    "success_criteria": ["path works"],
                    "max_attempts": 1,
                },
                {
                    "title": "Authority path",
                    "success_criteria": ["authority responds"],
                },
            ],
            max_attempts=3,
        )
        action_ids = {item["title"]: item["id"] for item in project["actions"]}
        first_id = action_ids["Exhausted path"]
        second_id = action_ids["Authority path"]
        self.store.claim_action(first_id, {"worker_id": "worker-a", "claim_key": "first-1"})
        self.store.record_result(
            first_id,
            {
                "worker_id": "worker-a",
                "attempt_key": "first-1",
                "outcome": "failed",
                "summary": "The bounded path is exhausted",
                "evidence": evidence("exhausted-path"),
            },
        )
        self.store.claim_action(second_id, {"worker_id": "worker-b", "claim_key": "second-1"})
        self.store.record_result(
            second_id,
            {
                "worker_id": "worker-b",
                "attempt_key": "second-1",
                "outcome": "blocked",
                "summary": "The external authority is unavailable",
                "evidence": evidence("authority-unavailable"),
                "obstacle_class": "HARD BLOCKER",
                "hard_reason": "unavailable_authority",
                "next_step": "Wait for the authority",
            },
        )
        view = self.store.get_project(project["id"])
        self.assertEqual(
            {item["status"] for item in view["actions"]},
            {"QUARANTINED", "APPROVAL_PENDING"},
        )
        self.assertEqual(view["blocker_truth"], "true")
        self.assertTrue(view["is_blocked"])

    def test_local_blocker_is_contained_and_ranker_selects_substitute(self):
        project = self.create_project(
            actions=[
                {
                    "title": "Unavailable preferred tool",
                    "success_criteria": ["artifact exists"],
                    "value": 12,
                },
                {
                    "title": "Available substitute",
                    "success_criteria": ["artifact exists"],
                    "value": 6,
                },
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "claim-1"})
        result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "claim-1",
                "outcome": "blocked",
                "summary": "Preferred tool missing",
                "evidence": evidence("missing-tool"),
                "obstacle_class": "LOCAL BLOCKER",
                "next_step": "Use substitute",
            },
        )
        self.assertEqual(result["project"]["closure"]["next"], "Available substitute")
        self.assertFalse(result["project"]["is_blocked"])

    def test_restart_and_claim_replay_do_not_spend_twice(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        first = self.store.claim_action(
            action_id, {"worker_id": "worker", "claim_key": "stable-claim"}
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            stores = list(pool.map(lambda _index: ClosureStore(self.path), range(8)))
        restarted = stores[0]
        replay = restarted.claim_action(
            action_id, {"worker_id": "worker", "claim_key": "stable-claim"}
        )
        self.assertFalse(first["idempotent"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(restarted.get_project(project["id"])["budget"]["attempts"], 1)

    def test_project_budget_does_not_quarantine_already_running_siblings(self):
        project = self.create_project(
            actions=[
                {"title": "First", "success_criteria": ["first passes"]},
                {"title": "Second", "success_criteria": ["second passes"]},
            ],
            max_attempts=2,
        )
        first, second = project["actions"]
        self.store.claim_action(first["id"], {"worker_id": "w1", "claim_key": "first-1"})
        self.store.claim_action(second["id"], {"worker_id": "w2", "claim_key": "second-1"})
        for action, worker, attempt in (
            (first, "w1", "first-1"),
            (second, "w2", "second-1"),
        ):
            result = self.store.record_result(
                action["id"],
                {
                    "worker_id": worker,
                    "attempt_key": attempt,
                    "outcome": "success",
                    "summary": "Acceptance evidence produced",
                    "evidence": evidence(f"{attempt}-result"),
                },
            )
            closed = self.store.verify_action(
                action["id"],
                {
                    "verifier_id": f"verify-{worker}",
                    "attempt_key": attempt,
                    "result_receipt_id": result["receipt"]["id"],
                    "verdict": "pass",
                    "summary": "Acceptance independently reproduced",
                    "evidence": evidence(f"{attempt}-verify"),
                },
            )
        self.assertEqual(closed["project"]["status"], "PASSED")

    def test_retry_cannot_consume_attempt_reserved_for_unstarted_required_action(self):
        project = self.create_project(
            actions=[
                {"title": "A", "success_criteria": ["A passes"]},
                {"title": "B", "success_criteria": ["B passes"]},
            ],
            max_attempts=2,
        )
        first, second = project["actions"]
        self.store.claim_action(first["id"], {"worker_id": "worker-a", "claim_key": "a-1"})
        self.store.record_result(
            first["id"],
            {
                "worker_id": "worker-a",
                "attempt_key": "a-1",
                "outcome": "failed",
                "summary": "A needs a retry",
                "evidence": evidence("a-failed"),
            },
        )
        with self.assertRaisesRegex(ClosureError, "reserved for required actions"):
            self.store.claim_action(
                first["id"], {"worker_id": "worker-a", "claim_key": "a-2"}
            )
        claimed_second = self.store.claim_action(
            second["id"], {"worker_id": "worker-b", "claim_key": "b-1"}
        )
        self.assertEqual(claimed_second["project"]["budget"]["attempts"], 2)
        states = {action["title"]: action["status"] for action in claimed_second["project"]["actions"]}
        self.assertEqual(states["B"], "RUNNING")

    def test_stale_result_from_prior_attempt_is_rejected(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-1"})
        self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "attempt-1",
                "outcome": "failed",
                "summary": "Retry needed",
                "evidence": evidence("first-failure"),
            },
        )
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-2"})
        with self.assertRaisesRegex(ClosureError, "current attempt"):
            self.store.record_result(
                action_id,
                {
                    "worker_id": "worker",
                    "attempt_key": "attempt-1",
                    "outcome": "success",
                    "summary": "Delayed old result",
                    "evidence": evidence("stale-result"),
                },
            )

    def test_historical_claim_key_cannot_resume_current_suspended_attempt(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "old"})
        self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "old",
                "outcome": "failed",
                "summary": "Retry",
                "evidence": evidence("old-failed"),
            },
        )
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "current"})
        self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "current",
                "outcome": "blocked",
                "summary": "Local dependency is absent",
                "evidence": evidence("current-blocked"),
                "obstacle_class": "LOCAL BLOCKER",
            },
        )
        with self.assertRaisesRegex(ClosureError, "historical claim_key"):
            self.store.claim_action(
                action_id,
                {
                    "worker_id": "worker",
                    "claim_key": "old",
                    "resolution_evidence": evidence("stale-resolution"),
                },
            )
        view = self.store.get_project(project["id"])
        self.assertEqual(view["actions"][0]["status"], "LOCAL_BLOCKED")

    def test_stale_verdict_and_result_receipt_are_rejected(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-1"})
        first = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "attempt-1",
                "outcome": "success",
                "summary": "First result",
                "evidence": evidence("first-result"),
            },
        )
        self.store.verify_action(
            action_id,
            {
                "verifier_id": "verifier",
                "attempt_key": "attempt-1",
                "result_receipt_id": first["receipt"]["id"],
                "verdict": "fail",
                "summary": "Needs correction",
                "evidence": evidence("first-verdict"),
            },
        )
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-2"})
        second = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "attempt-2",
                "outcome": "success",
                "summary": "Corrected result",
                "evidence": evidence("second-result"),
            },
        )
        with self.assertRaisesRegex(ClosureError, "current attempt"):
            self.store.verify_action(
                action_id,
                {
                    "verifier_id": "verifier",
                    "attempt_key": "attempt-1",
                    "result_receipt_id": first["receipt"]["id"],
                    "verdict": "pass",
                    "summary": "Delayed verdict",
                    "evidence": evidence("stale-verdict"),
                },
            )
        with self.assertRaisesRegex(ClosureError, "current worker-result receipt"):
            self.store.verify_action(
                action_id,
                {
                    "verifier_id": "verifier",
                    "attempt_key": "attempt-2",
                    "result_receipt_id": first["receipt"]["id"],
                    "verdict": "pass",
                    "summary": "Wrong receipt",
                    "evidence": evidence("wrong-receipt"),
                },
            )
        self.assertNotEqual(first["receipt"]["id"], second["receipt"]["id"])

    def test_concurrent_duplicate_effect_has_one_reservation(self):
        project = self.create_project(
            actions=[
                {
                    "title": "Apply reversible change",
                    "success_criteria": ["change applied"],
                    "risk": "reversible",
                }
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "claim-1"})

        def reserve(_index: int):
            return ClosureStore(self.path).reserve_effect(
                action_id,
                {
                    "idempotency_key": "effect-once",
                    "attempt_key": "claim-1",
                    "effect_kind": "patch",
                    "target": "workspace://fixture/file",
                    "payload_digest": "c" * 64,
                },
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(16)))
        self.assertEqual(sum(not item["idempotent"] for item in results), 1)
        self.assertEqual(sum(item["idempotent"] for item in results), 15)
        self.assertEqual(self.store.get_project(project["id"])["budget"]["effects"], 1)

    def test_read_only_action_cannot_reserve_effect(self):
        project = self.create_project(
            actions=[
                {"title": "Inspect", "success_criteria": ["report exists"], "risk": "read_only"}
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "inspect-1"})
        with self.assertRaisesRegex(ClosureError, "Read-only work"):
            self.store.reserve_effect(
                action_id,
                {
                    "idempotency_key": "inspect-effect",
                    "attempt_key": "inspect-1",
                    "effect_kind": "inspect",
                    "target": "workspace://fixture",
                    "payload_digest": "e" * 64,
                },
            )

    def test_same_effect_digest_cannot_hide_behind_another_key(self):
        project = self.create_project(
            actions=[
                {"title": "Change", "success_criteria": ["changed"], "risk": "reversible"}
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "change-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "effect-a",
                "attempt_key": "change-1",
                "effect_kind": "patch",
                "target": "workspace://fixture/file",
                "payload_digest": "f" * 64,
            },
        )
        with self.assertRaisesRegex(ClosureError, "already has a reservation"):
            self.store.reserve_effect(
                action_id,
                {
                    "idempotency_key": "effect-b",
                    "attempt_key": "change-1",
                    "effect_kind": "patch",
                    "target": "workspace://fixture/file",
                    "payload_digest": "f" * 64,
                },
            )

    def test_effect_lifecycle_distinguishes_dispatch_from_completion(self):
        project = self.create_project(
            actions=[
                {"title": "Change", "success_criteria": ["changed"], "risk": "reversible"}
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "change-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "effect-life",
                "attempt_key": "change-1",
                "effect_kind": "patch",
                "target": "workspace://fixture/file",
                "payload_digest": "1" * 64,
            },
        )
        dispatched = self.store.mark_effect(
            "effect-life", {"adapter_id": "adapter", "status": "DISPATCHED"}
        )
        self.assertEqual(dispatched["reservation"]["status"], "DISPATCHED")
        self.assertEqual(self.store.health()["status"], "degraded")
        completed = self.store.mark_effect(
            "effect-life",
            {
                "adapter_id": "adapter",
                "status": "COMPLETED",
                "result_digest": "2" * 64,
                "dispatch_token": dispatched["reservation"]["dispatch_token"],
                "evidence": evidence("effect-complete"),
            },
        )
        self.assertEqual(completed["reservation"]["status"], "COMPLETED")
        self.assertEqual(self.store.health()["status"], "ok")

    def test_lost_dispatch_response_is_replayable_by_same_adapter(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "replay-dispatch",
                "attempt_key": "attempt-1",
                "effect_kind": "patch",
                "target": "workspace://fixture/replay",
                "payload_digest": "3" * 64,
            },
        )
        dispatched = self.store.mark_effect(
            "replay-dispatch", {"adapter_id": "adapter", "status": "DISPATCHED"}
        )
        replay = self.store.mark_effect(
            "replay-dispatch", {"adapter_id": "adapter", "status": "DISPATCHED"}
        )
        self.assertTrue(replay["idempotent"])
        self.assertEqual(
            replay["reservation"]["dispatch_token"],
            dispatched["reservation"]["dispatch_token"],
        )

    def test_verifier_failure_closes_undispatched_effect_before_retry(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "old-effect",
                "attempt_key": "attempt-1",
                "effect_kind": "patch",
                "target": "workspace://fixture/old",
                "payload_digest": "4" * 64,
            },
        )
        result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "attempt-1",
                "outcome": "success",
                "summary": "Ready for verification",
                "evidence": evidence("old-result"),
            },
        )
        self.store.verify_action(
            action_id,
            {
                "verifier_id": "verifier",
                "attempt_key": "attempt-1",
                "result_receipt_id": result["receipt"]["id"],
                "verdict": "fail",
                "summary": "Needs retry",
                "evidence": evidence("old-verdict"),
            },
        )
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-2"})
        with self.assertRaisesRegex(ClosureError, "FAILED to DISPATCHED"):
            self.store.mark_effect(
                "old-effect", {"adapter_id": "adapter", "status": "DISPATCHED"}
            )

    def test_failed_effect_may_retry_same_descriptor_on_a_new_attempt(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        descriptor = {
            "effect_kind": "patch",
            "target": "workspace://fixture/retry",
            "payload_digest": "7" * 64,
        }
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-1"})
        self.store.reserve_effect(
            action_id,
            {"idempotency_key": "retry-effect-1", "attempt_key": "attempt-1", **descriptor},
        )
        self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "attempt-1",
                "outcome": "failed",
                "summary": "Effect was never dispatched",
                "evidence": evidence("effect-not-dispatched"),
            },
        )
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-2"})
        retried = self.store.reserve_effect(
            action_id,
            {"idempotency_key": "retry-effect-2", "attempt_key": "attempt-2", **descriptor},
        )
        self.assertFalse(retried["idempotent"])
        self.assertEqual(retried["reservation"]["attempt_key"], "attempt-2")

    def test_same_attempt_resume_reactivates_known_undispatched_effect(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        effect_request = {
            "idempotency_key": "resume-effect",
            "attempt_key": "resume-1",
            "effect_kind": "patch",
            "target": "workspace://fixture/resume",
            "payload_digest": "6" * 64,
        }
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "resume-1"})
        self.store.reserve_effect(action_id, effect_request)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """UPDATE effect_reservations SET created_at = '2000-01-01T00:00:00+00:00'
                   WHERE idempotency_key = 'resume-effect'"""
            )
        self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "resume-1",
                "outcome": "blocked",
                "summary": "A local input is temporarily unavailable",
                "evidence": evidence("resume-blocker"),
                "obstacle_class": "LOCAL BLOCKER",
                "next_step": "Restore the local input",
            },
        )
        self.store.claim_action(
            action_id,
            {
                "worker_id": "worker",
                "claim_key": "resume-1",
                "resolution_evidence": evidence("resume-resolution"),
            },
        )
        reactivated = self.store.reserve_effect(action_id, effect_request)
        self.assertTrue(reactivated["reactivated"])
        self.assertEqual(reactivated["reservation"]["status"], "RESERVED")
        self.assertEqual(reactivated["project"]["budget"]["effects"], 1)
        health = self.store.health()
        self.assertTrue(health["layers"]["control"]["materialization_ok"])
        self.assertEqual(health["layers"]["workflow"]["stale_reservations"], 0)

    def test_completed_irreversible_effect_is_adopted_by_retry_without_redispatch(self):
        project = self.create_project(
            actions=[
                {
                    "title": "Publish",
                    "success_criteria": ["release is visible"],
                    "risk": "irreversible",
                }
            ]
        )
        action_id = project["actions"][0]["id"]
        descriptor = {
            "effect_kind": "publish",
            "target": "release://fixture/adoption",
            "payload_digest": "7" * 64,
            "approval_ref": "approval://owner/release",
        }
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "publish-1"})
        self.store.reserve_effect(
            action_id,
            {"idempotency_key": "publish-effect-1", "attempt_key": "publish-1", **descriptor},
        )
        dispatched = self.store.mark_effect(
            "publish-effect-1", {"adapter_id": "adapter", "status": "DISPATCHED"}
        )
        self.store.mark_effect(
            "publish-effect-1",
            {
                "adapter_id": "adapter",
                "status": "COMPLETED",
                "result_digest": "8" * 64,
                "dispatch_token": dispatched["reservation"]["dispatch_token"],
                "evidence": evidence("published-once"),
            },
        )
        first_result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "publish-1",
                "outcome": "success",
                "summary": "Published",
                "evidence": evidence("publish-result-1"),
            },
        )
        self.store.verify_action(
            action_id,
            {
                "verifier_id": "verifier",
                "attempt_key": "publish-1",
                "result_receipt_id": first_result["receipt"]["id"],
                "verdict": "fail",
                "summary": "Evidence needs a fresh capture; do not republish",
                "evidence": evidence("publish-verdict-1"),
            },
        )
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "publish-2"})
        adopted = self.store.reserve_effect(
            action_id,
            {"idempotency_key": "publish-effect-2", "attempt_key": "publish-2", **descriptor},
        )
        self.assertTrue(adopted["adopted"])
        self.assertTrue(adopted["already_completed"])
        self.assertEqual(adopted["project"]["budget"]["effects"], 1)
        second_result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "publish-2",
                "outcome": "success",
                "summary": "Reused the completed publication and refreshed evidence",
                "evidence": evidence("publish-result-2"),
            },
        )
        verified = self.store.verify_action(
            action_id,
            {
                "verifier_id": "verifier",
                "attempt_key": "publish-2",
                "result_receipt_id": second_result["receipt"]["id"],
                "verdict": "pass",
                "summary": "Existing release independently verified",
                "evidence": evidence("publish-verdict-2"),
            },
        )
        self.assertEqual(verified["project"]["status"], "PASSED")
        self.assertTrue(self.store.health()["layers"]["control"]["materialization_ok"])

    def test_unresolved_current_effect_prevents_passing_verification(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "unresolved-effect",
                "attempt_key": "attempt-1",
                "effect_kind": "patch",
                "target": "workspace://fixture/unresolved",
                "payload_digest": "8" * 64,
            },
        )
        result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "attempt-1",
                "outcome": "success",
                "summary": "Worker result is ready",
                "evidence": evidence("unresolved-result"),
            },
        )
        with self.assertRaisesRegex(ClosureError, "every reserved effect"):
            self.store.verify_action(
                action_id,
                {
                    "verifier_id": "verifier",
                    "attempt_key": "attempt-1",
                    "result_receipt_id": result["receipt"]["id"],
                    "verdict": "pass",
                    "summary": "Would otherwise pass",
                    "evidence": evidence("unresolved-verify"),
                },
            )

    def test_halt_prevents_new_dispatch_but_unknown_effect_can_be_reconciled(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "halted-effect",
                "attempt_key": "attempt-1",
                "effect_kind": "patch",
                "target": "workspace://fixture/halted",
                "payload_digest": "9" * 64,
            },
        )
        self.store.halt_project(project["id"], {"operator_id": "owner", "reason": "Stop"})
        with self.assertRaisesRegex(ClosureError, "cannot dispatch"):
            self.store.mark_effect(
                "halted-effect", {"adapter_id": "adapter", "status": "DISPATCHED"}
            )

        second = self.create_project()
        second_action = second["actions"][0]["id"]
        self.store.claim_action(
            second_action, {"worker_id": "worker", "claim_key": "attempt-unknown"}
        )
        self.store.reserve_effect(
            second_action,
            {
                "idempotency_key": "unknown-effect",
                "attempt_key": "attempt-unknown",
                "effect_kind": "patch",
                "target": "workspace://fixture/unknown",
                "payload_digest": "a" * 64,
            },
        )
        dispatched = self.store.mark_effect(
            "unknown-effect", {"adapter_id": "adapter", "status": "DISPATCHED"}
        )
        self.store.mark_effect(
            "unknown-effect",
            {
                "adapter_id": "adapter",
                "status": "UNKNOWN",
                "dispatch_token": dispatched["reservation"]["dispatch_token"],
                "result_digest": "b" * 64,
                "evidence": evidence("unknown-effect"),
            },
        )
        reconciled = self.store.reconcile_unknown_effect(
            "unknown-effect",
            {
                "operator_id": "owner",
                "authorization_ref": "approval://reconcile",
                "decision": "COMPLETED",
                "result_digest": "c" * 64,
                "evidence": evidence("operator-reconciliation"),
            },
        )
        self.assertEqual(reconciled["reservation"]["status"], "COMPLETED")
        self.assertEqual(self.store.health()["layers"]["outcome"]["unresolved_effects"], 0)

    def test_operator_reconciliation_replay_returns_original_receipt(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "reconcile-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "reconcile-effect",
                "attempt_key": "reconcile-1",
                "effect_kind": "patch",
                "target": "workspace://fixture/reconcile",
                "payload_digest": "a" * 64,
            },
        )
        dispatched = self.store.mark_effect(
            "reconcile-effect", {"adapter_id": "adapter", "status": "DISPATCHED"}
        )
        self.store.mark_effect(
            "reconcile-effect",
            {
                "adapter_id": "adapter",
                "status": "UNKNOWN",
                "result_digest": "b" * 64,
                "dispatch_token": dispatched["reservation"]["dispatch_token"],
                "evidence": evidence("reconcile-unknown"),
            },
        )
        request = {
            "operator_id": "operator",
            "authorization_ref": "approval://reconcile/replay",
            "decision": "COMPLETED",
            "result_digest": "c" * 64,
            "evidence": evidence("reconcile-decision"),
        }
        first = self.store.reconcile_unknown_effect("reconcile-effect", request)
        replay = self.store.reconcile_unknown_effect("reconcile-effect", request)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["receipt"]["id"], first["receipt"]["id"])
        with self.assertRaisesRegex(ClosureError, "different operator reconciliation"):
            self.store.reconcile_unknown_effect(
                "reconcile-effect", {**request, "result_digest": "d" * 64}
            )

    def test_irreversible_effect_is_denied_until_approval(self):
        project = self.create_project(
            actions=[
                {
                    "title": "Publish",
                    "success_criteria": ["release visible"],
                    "risk": "irreversible",
                }
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "claim-1"})
        with self.assertRaisesRegex(ClosureError, "requires explicit approval"):
            self.store.reserve_effect(
                action_id,
                {
                    "idempotency_key": "publish-once",
                    "attempt_key": "claim-1",
                    "effect_kind": "publish",
                    "target": "release://fixture",
                    "payload_digest": "d" * 64,
                },
            )
        pending = self.store.get_project(project["id"])
        self.assertEqual(pending["status"], "APPROVAL_PENDING")
        self.assertEqual(pending["budget"]["effects"], 0)

    def test_irreversible_action_cannot_pass_without_completed_approved_effect(self):
        project = self.create_project(
            actions=[
                {"title": "Publish", "success_criteria": ["visible"], "risk": "irreversible"}
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "publish-1"})
        result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "publish-1",
                "outcome": "success",
                "summary": "Claimed publication without an effect",
                "evidence": evidence("unsupported-publication"),
            },
        )
        with self.assertRaisesRegex(ClosureError, "completed, approval-bearing effect"):
            self.store.verify_action(
                action_id,
                {
                    "verifier_id": "verifier",
                    "attempt_key": "publish-1",
                    "result_receipt_id": result["receipt"]["id"],
                    "verdict": "pass",
                    "summary": "Would otherwise pass",
                    "evidence": evidence("unsupported-publication-verdict"),
                },
            )
        self.assertNotEqual(self.store.get_project(project["id"])["status"], "PASSED")

    def test_forged_effect_approval_projection_cannot_authorize_a_pass(self):
        project = self.create_project(
            actions=[
                {"title": "Publish", "success_criteria": ["visible"], "risk": "irreversible"}
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "approval-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "approval-effect",
                "attempt_key": "approval-1",
                "effect_kind": "publish",
                "target": "release://fixture/approval",
                "payload_digest": "d" * 64,
                "approval_ref": "approval://owner/original",
            },
        )
        dispatched = self.store.mark_effect(
            "approval-effect", {"adapter_id": "adapter", "status": "DISPATCHED"}
        )
        self.store.mark_effect(
            "approval-effect",
            {
                "adapter_id": "adapter",
                "status": "COMPLETED",
                "result_digest": "e" * 64,
                "dispatch_token": dispatched["reservation"]["dispatch_token"],
                "evidence": evidence("approval-effect-complete"),
            },
        )
        result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "approval-1",
                "outcome": "success",
                "summary": "Ready for verification",
                "evidence": evidence("approval-result"),
            },
        )
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TRIGGER effects_identity_no_update")
            conn.execute(
                """UPDATE effect_reservations SET approval_ref = 'approval://forged'
                   WHERE idempotency_key = 'approval-effect'"""
            )
        with self.assertRaisesRegex(ClosureError, "receipt-backed effect record"):
            self.store.verify_action(
                action_id,
                {
                    "verifier_id": "verifier",
                    "attempt_key": "approval-1",
                    "result_receipt_id": result["receipt"]["id"],
                    "verdict": "pass",
                    "summary": "Forged approval must not pass",
                    "evidence": evidence("approval-verdict"),
                },
            )
        materialization = self.store.health()["layers"]["control"]["materialization"]
        self.assertGreater(materialization["effect_receipt_mismatches"], 0)

    def test_late_approval_resumes_same_attempt_without_extra_spend(self):
        project = self.create_project(
            actions=[
                {"title": "Publish", "success_criteria": ["visible"], "risk": "irreversible"}
            ]
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "publish-1"})
        effect_request = {
            "idempotency_key": "publish-effect",
            "attempt_key": "publish-1",
            "effect_kind": "publish",
            "target": "release://fixture",
            "payload_digest": "d" * 64,
        }
        with self.assertRaises(ClosureError):
            self.store.reserve_effect(action_id, effect_request)
        resumed = self.store.claim_action(
            action_id,
            {
                "worker_id": "worker",
                "claim_key": "publish-1",
                "approval_ref": "approval://owner",
                "resolution_evidence": evidence("owner-approved"),
            },
        )
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["project"]["budget"]["attempts"], 1)
        reserved = self.store.reserve_effect(
            action_id, {**effect_request, "approval_ref": "approval://owner"}
        )
        self.assertEqual(reserved["reservation"]["attempt_key"], "publish-1")

    def test_receipts_are_append_only_and_chain_verifies(self):
        project = self.create_project()
        self.assertTrue(self.store.verify_receipt_chain()["ok"])
        with self.assertRaises(sqlite3.IntegrityError):
            with sqlite3.connect(self.path) as conn:
                conn.execute("UPDATE receipts SET kind = 'tampered'")
        self.assertTrue(self.store.verify_receipt_chain()["ok"])
        receipts = self.store.receipts(project["id"])
        self.assertGreaterEqual(len(receipts), 1)

    def test_receipts_redact_secret_shaped_text(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "claim-1"})
        self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "claim-1",
                "outcome": "failed",
                "summary": "token=sk-abcdefghijklmnop must not persist",
                "evidence": evidence("redaction"),
            },
        )
        serialized = str(self.store.receipts(project["id"]))
        self.assertNotIn("sk-abcdefghijklmnop", serialized)
        self.assertIn("[REDACTED]", serialized)
        with sqlite3.connect(self.path) as conn:
            database_dump = "\n".join(conn.iterdump())
        self.assertNotIn("sk-abcdefghijklmnop", database_dump)

    def test_secret_shaped_identity_is_rejected_before_persistence(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        secret = "token=sk-abcdefghijklmnop"
        with self.assertRaisesRegex(ValueError, "secret-shaped"):
            self.store.claim_action(
                action_id, {"worker_id": secret, "claim_key": "safe-claim"}
            )
        with sqlite3.connect(self.path) as conn:
            database_dump = "\n".join(conn.iterdump())
            claim_count = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        self.assertNotIn("sk-abcdefghijklmnop", database_dump)
        self.assertEqual(claim_count, 0)

    def test_operator_halt_is_durable(self):
        project = self.create_project()
        halted = self.store.halt_project(
            project["id"], {"operator_id": "owner", "reason": "Contain the run"}
        )
        restarted = ClosureStore(self.path)
        self.assertEqual(halted["project"]["status"], "QUARANTINED")
        self.assertEqual(restarted.get_project(project["id"])["halted_reason"], "Contain the run")

    def test_exhausted_attempt_budget_persists_quarantine_and_receipt(self):
        project = self.create_project(
            actions=[
                {
                    "title": "One try",
                    "success_criteria": ["passes"],
                    "max_attempts": 3,
                }
            ],
            max_attempts=1,
        )
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "attempt-1"})
        self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "attempt-1",
                "outcome": "failed",
                "summary": "Did not pass",
                "evidence": evidence("attempt-failed"),
            },
        )
        with self.assertRaisesRegex(ClosureError, "QUARANTINED"):
            self.store.claim_action(
                action_id, {"worker_id": "worker", "claim_key": "attempt-2"}
            )
        view = ClosureStore(self.path).get_project(project["id"])
        self.assertEqual(view["status"], "QUARANTINED")
        self.assertTrue(any(item["status"] == "QUARANTINED" for item in view["actions"]))
        self.assertTrue(
            any(receipt["kind"] == "budget.paths_contained" for receipt in self.store.receipts(project["id"]))
        )

    def test_health_detects_materialized_state_bypass(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE actions SET status = 'PASSED', worker_id = 'fake', verifier_id = 'other' WHERE id = ?",
                (action_id,),
            )
            conn.execute("UPDATE projects SET status = 'PASSED' WHERE id = ?", (project["id"],))
        health = self.store.health()
        self.assertEqual(health["status"], "degraded")
        self.assertFalse(health["layers"]["control"]["materialization_ok"])
        self.assertGreater(
            health["layers"]["control"]["materialization"]["invalid_pass_receipts"], 0
        )

    def test_health_detects_nonterminal_state_projection_bypass(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """UPDATE actions SET status = 'RUNNING', worker_id = 'fake-worker',
                   attempt_key = 'fake-attempt' WHERE id = ?""",
                (action_id,),
            )
            conn.execute("UPDATE projects SET status = 'RUNNING' WHERE id = ?", (project["id"],))
        materialization = self.store.health()["layers"]["control"]["materialization"]
        self.assertGreater(materialization["action_state_projection_mismatches"], 0)
        self.assertGreater(materialization["action_identity_projection_mismatches"], 0)

    def test_health_detects_obstacle_projection_bypass(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "claim-1"})
        self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "claim-1",
                "outcome": "blocked",
                "summary": "Local dependency is absent",
                "evidence": evidence("local-obstacle"),
                "obstacle_class": "LOCAL BLOCKER",
            },
        )
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TRIGGER obstacles_contract_no_update")
            conn.execute(
                """UPDATE obstacles SET obstacle_class = 'HARD BLOCKER',
                   hard_reason = 'safety_boundary' WHERE action_id = ?""",
                (action_id,),
            )
        view = self.store.get_project(project["id"])
        health = self.store.health()
        self.assertEqual(view["blocker_truth"], "true")
        self.assertGreater(
            health["layers"]["control"]["materialization"]["obstacle_projection_mismatches"],
            0,
        )

    def test_health_detects_contract_and_budget_projection_bypass(self):
        project = self.create_project(max_attempts=3, max_effects=1)
        action_id = project["actions"][0]["id"]
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TRIGGER projects_contract_no_update")
            conn.execute("DROP TRIGGER actions_contract_no_update")
            conn.execute(
                """UPDATE projects SET objective = 'forged', closed_when_json = '[\"forged\"]',
                   max_attempts = 999, max_effects = 999 WHERE id = ?""",
                (project["id"],),
            )
            conn.execute(
                """UPDATE actions SET risk = 'read_only', max_attempts = 999,
                   remaining_effort = 0.001 WHERE id = ?""",
                (action_id,),
            )
        materialization = self.store.health()["layers"]["control"]["materialization"]
        self.assertGreater(materialization["project_contract_projection_mismatches"], 0)
        self.assertGreater(materialization["action_contract_projection_mismatches"], 0)

    def test_ratified_contract_rows_and_effect_obligations_reject_deletion_or_rewrite(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "immutable-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "immutable-effect",
                "attempt_key": "immutable-1",
                "effect_kind": "patch",
                "target": "workspace://fixture/immutable",
                "payload_digest": "b" * 64,
            },
        )
        with sqlite3.connect(self.path) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE projects SET objective = 'forged' WHERE id = ?", (project["id"],))
        with sqlite3.connect(self.path) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM actions WHERE id = ?", (action_id,))
        with sqlite3.connect(self.path) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM effect_reservations WHERE idempotency_key = 'immutable-effect'"
            )

    def test_deleted_effect_row_cannot_erase_receipt_backed_verification_obligation(self):
        project = self.create_project()
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "delete-1"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "delete-effect",
                "attempt_key": "delete-1",
                "effect_kind": "patch",
                "target": "workspace://fixture/delete",
                "payload_digest": "c" * 64,
            },
        )
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TRIGGER effects_no_delete")
            conn.execute(
                "DELETE FROM effect_reservations WHERE idempotency_key = 'delete-effect'"
            )
        result = self.store.record_result(
            action_id,
            {
                "worker_id": "worker",
                "attempt_key": "delete-1",
                "outcome": "success",
                "summary": "Result cannot erase an effect obligation",
                "evidence": evidence("delete-result"),
            },
        )
        with self.assertRaisesRegex(ClosureError, "receipt-backed effect record"):
            self.store.verify_action(
                action_id,
                {
                    "verifier_id": "verifier",
                    "attempt_key": "delete-1",
                    "result_receipt_id": result["receipt"]["id"],
                    "verdict": "pass",
                    "summary": "Must remain open",
                    "evidence": evidence("delete-verdict"),
                },
            )
        materialization = self.store.health()["layers"]["control"]["materialization"]
        self.assertGreater(materialization["effect_row_bijection_mismatches"], 0)

    def test_deleted_ratified_action_forces_project_quarantine(self):
        project = self.create_project(
            actions=[
                {"title": "Keep", "success_criteria": ["kept"]},
                {"title": "Delete", "success_criteria": ["still required"]},
            ]
        )
        keep_id, delete_id = [item["id"] for item in project["actions"]]
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TRIGGER actions_no_delete")
            conn.execute("DELETE FROM actions WHERE id = ?", (delete_id,))
        self.store.claim_action(keep_id, {"worker_id": "worker", "claim_key": "keep-1"})
        result = self.store.record_result(
            keep_id,
            {
                "worker_id": "worker",
                "attempt_key": "keep-1",
                "outcome": "success",
                "summary": "The surviving action is complete",
                "evidence": evidence("keep-result"),
            },
        )
        verified = self.store.verify_action(
            keep_id,
            {
                "verifier_id": "verifier",
                "attempt_key": "keep-1",
                "result_receipt_id": result["receipt"]["id"],
                "verdict": "pass",
                "summary": "Surviving action passes but contract does not",
                "evidence": evidence("keep-verdict"),
            },
        )
        self.assertEqual(verified["project"]["status"], "QUARANTINED")
        materialization = self.store.health()["layers"]["control"]["materialization"]
        self.assertGreater(materialization["action_contract_projection_mismatches"], 0)

    def test_legacy_contract_binding_preserves_original_action_membership(self):
        project = self.create_project(
            actions=[
                {"title": "Required A", "success_criteria": ["A passes"]},
                {"title": "Required B", "success_criteria": ["B passes"]},
            ]
        )
        original_ids = {item["id"] for item in project["actions"]}
        original_objective = project["objective"]
        original_max_attempts = project["budget"]["max_attempts"]
        deleted_id = project["actions"][0]["id"]
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("DROP TRIGGER receipts_no_update")
            conn.execute("DROP TRIGGER actions_no_delete")
            conn.execute("DROP TRIGGER projects_contract_no_update")
            receipt = conn.execute(
                "SELECT * FROM receipts WHERE kind = 'contract.ratified'"
            ).fetchone()
            payload = json.loads(receipt["payload_json"])
            payload.pop("actions")
            conn.execute(
                "UPDATE receipts SET payload_json = ? WHERE seq = ?",
                (
                    json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                    receipt["seq"],
                ),
            )
            conn.execute("UPDATE receipts SET receipt_hash = 'legacy-temp-' || seq")
            previous = "GENESIS"
            for row in conn.execute("SELECT * FROM receipts ORDER BY seq").fetchall():
                envelope = {
                    "project_id": row["project_id"],
                    "kind": row["kind"],
                    "payload": json.loads(row["payload_json"]),
                    "prev_hash": previous,
                    "created_at": row["created_at"],
                }
                receipt_hash = hashlib.sha256(
                    json.dumps(
                        envelope,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode()
                ).hexdigest()
                conn.execute(
                    "UPDATE receipts SET prev_hash = ?, receipt_hash = ? WHERE seq = ?",
                    (previous, receipt_hash, row["seq"]),
                )
                previous = receipt_hash
            conn.execute(
                """UPDATE projects SET objective = 'forged legacy objective',
                   max_attempts = 999 WHERE id = ?""",
                (project["id"],),
            )
            conn.execute("DELETE FROM actions WHERE id = ?", (deleted_id,))
            conn.execute("DROP TABLE closure_schema_meta")

        restarted = ClosureStore(self.path)
        self.assertEqual(restarted.get_project(project["id"])["status"], "QUARANTINED")
        materialization = restarted.health()["layers"]["control"]["materialization"]
        self.assertGreater(materialization["action_contract_projection_mismatches"], 0)
        self.assertGreater(materialization["project_contract_projection_mismatches"], 0)
        with sqlite3.connect(self.path) as conn:
            bound = json.loads(
                conn.execute(
                    """SELECT payload_json FROM receipts
                       WHERE kind = 'schema.contract_bound' ORDER BY seq DESC LIMIT 1"""
                ).fetchone()[0]
            )
        self.assertEqual(set(bound["source_action_ids"]), original_ids)
        self.assertEqual(set(bound["action_ids"]), original_ids)
        self.assertEqual(bound["objective"], original_objective)
        self.assertEqual(bound["budgets"]["max_attempts"], original_max_attempts)

    def test_restart_cannot_launder_completed_effect_identity_across_actions(self):
        project = self.create_project(
            actions=[
                {"title": "Reversible A", "success_criteria": ["A changed"]},
                {
                    "title": "Irreversible B",
                    "success_criteria": ["B published"],
                    "risk": "irreversible",
                },
            ]
        )
        action_ids = {item["title"]: item["id"] for item in project["actions"]}
        action_a = action_ids["Reversible A"]
        action_b = action_ids["Irreversible B"]
        self.store.claim_action(action_a, {"worker_id": "worker-a", "claim_key": "a-1"})
        descriptor = {
            "effect_kind": "patch",
            "target": "workspace://fixture/launder",
            "payload_digest": "f" * 64,
        }
        self.store.reserve_effect(
            action_a,
            {"idempotency_key": "launder-effect", "attempt_key": "a-1", **descriptor},
        )
        dispatched = self.store.mark_effect(
            "launder-effect", {"adapter_id": "adapter", "status": "DISPATCHED"}
        )
        self.store.mark_effect(
            "launder-effect",
            {
                "adapter_id": "adapter",
                "status": "COMPLETED",
                "result_digest": "1" * 64,
                "dispatch_token": dispatched["reservation"]["dispatch_token"],
                "evidence": evidence("launder-effect-complete"),
            },
        )
        result_a = self.store.record_result(
            action_a,
            {
                "worker_id": "worker-a",
                "attempt_key": "a-1",
                "outcome": "success",
                "summary": "A is complete",
                "evidence": evidence("launder-a-result"),
            },
        )
        self.store.verify_action(
            action_a,
            {
                "verifier_id": "verifier-a",
                "attempt_key": "a-1",
                "result_receipt_id": result_a["receipt"]["id"],
                "verdict": "pass",
                "summary": "A verified",
                "evidence": evidence("launder-a-verdict"),
            },
        )
        self.store.claim_action(action_b, {"worker_id": "worker-b", "claim_key": "b-1"})
        forged_descriptor = {"action_id": action_b, **descriptor}
        forged_digest = hashlib.sha256(
            json.dumps(
                forged_descriptor,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TRIGGER effects_identity_no_update")
            conn.execute(
                """UPDATE effect_reservations
                   SET action_id = ?, attempt_key = 'b-1', effect_digest = ?,
                       approval_ref = 'approval://forged'
                   WHERE idempotency_key = 'launder-effect'""",
                (action_b, forged_digest),
            )

        restarted = ClosureStore(self.path)
        view = restarted.get_project(project["id"])
        self.assertEqual(view["status"], "QUARANTINED")
        self.assertEqual({item["status"] for item in view["actions"]}, {"QUARANTINED"})
        with sqlite3.connect(self.path) as conn:
            effect = conn.execute(
                "SELECT status FROM effect_reservations WHERE idempotency_key = 'launder-effect'"
            ).fetchone()[0]
            laundering_bindings = conn.execute(
                """SELECT COUNT(*) FROM receipts WHERE kind = 'schema.effect_bound'
                   AND json_extract(payload_json, '$.idempotency_key') = 'launder-effect'"""
            ).fetchone()[0]
        self.assertEqual(effect, "UNKNOWN")
        self.assertEqual(laundering_bindings, 0)
        health = restarted.health()
        self.assertEqual(health["status"], "degraded")
        self.assertGreater(
            health["layers"]["control"]["materialization"]["effect_receipt_mismatches"],
            0,
        )

    def test_intermediate_schema_migrates_idempotently_and_quarantines_legacy_effect(self):
        project = self.create_project(request_key="legacy-create")
        action_id = project["actions"][0]["id"]
        self.store.claim_action(action_id, {"worker_id": "worker", "claim_key": "legacy-claim"})
        self.store.reserve_effect(
            action_id,
            {
                "idempotency_key": "legacy-effect",
                "attempt_key": "legacy-claim",
                "effect_kind": "patch",
                "target": "workspace://legacy/file",
                "payload_digest": "e" * 64,
            },
        )
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("DROP TRIGGER receipts_no_update")
            for receipt in conn.execute("SELECT * FROM receipts ORDER BY seq").fetchall():
                payload = json.loads(receipt["payload_json"])
                if receipt["kind"] == "action.claimed":
                    payload["claim_key"] = payload.pop("attempt_key")
                elif receipt["kind"] == "effect.reserved":
                    payload.pop("attempt_key", None)
                conn.execute(
                    "UPDATE receipts SET payload_json = ? WHERE seq = ?",
                    (
                        json.dumps(
                            payload,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=True,
                        ),
                        receipt["seq"],
                    ),
                )
            conn.execute("UPDATE receipts SET receipt_hash = 'legacy-temp-' || seq")
            previous = "GENESIS"
            for receipt in conn.execute("SELECT * FROM receipts ORDER BY seq").fetchall():
                payload = json.loads(receipt["payload_json"])
                envelope = {
                    "project_id": receipt["project_id"],
                    "kind": receipt["kind"],
                    "payload": payload,
                    "prev_hash": previous,
                    "created_at": receipt["created_at"],
                }
                receipt_hash = hashlib.sha256(
                    json.dumps(
                        envelope,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode()
                ).hexdigest()
                conn.execute(
                    "UPDATE receipts SET prev_hash = ?, receipt_hash = ? WHERE seq = ?",
                    (previous, receipt_hash, receipt["seq"]),
                )
                previous = receipt_hash
            conn.executescript(
                """
                DROP INDEX IF EXISTS idx_effect_attempt_digest;
                DROP TRIGGER IF EXISTS actions_no_delete;
                DROP TRIGGER IF EXISTS actions_contract_no_update;
                DROP TRIGGER IF EXISTS effects_no_delete;
                DROP TRIGGER IF EXISTS effects_identity_no_update;
                DROP TRIGGER IF EXISTS effect_adoptions_no_delete;
                DROP TRIGGER IF EXISTS effect_adoptions_no_update;
                DROP TRIGGER IF EXISTS project_requests_no_update;
                DROP TRIGGER IF EXISTS project_requests_no_delete;
                DROP TABLE IF EXISTS effect_adoptions;
                ALTER TABLE project_requests RENAME TO project_requests_current;
                CREATE TABLE project_requests (
                    request_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );
                INSERT INTO project_requests(request_key, project_id, created_at)
                    SELECT request_key, project_id, created_at FROM project_requests_current;
                DROP TABLE project_requests_current;

                ALTER TABLE actions DROP COLUMN attempt_key;
                ALTER TABLE actions DROP COLUMN result_receipt_id;

                ALTER TABLE effect_reservations RENAME TO effect_reservations_current;
                CREATE TABLE effect_reservations (
                    idempotency_key TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
                    effect_digest TEXT NOT NULL,
                    approval_ref TEXT,
                    receipt_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                INSERT INTO effect_reservations
                    SELECT idempotency_key, action_id, effect_digest, approval_ref,
                           receipt_id, created_at
                    FROM effect_reservations_current;
                DROP TABLE effect_reservations_current;
                CREATE UNIQUE INDEX idx_effect_action_digest
                    ON effect_reservations(action_id, effect_digest);
                DROP TABLE closure_schema_meta;
                """
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            migrated_stores = list(pool.map(lambda _index: ClosureStore(self.path), range(8)))
        restarted = migrated_stores[0]
        replay = restarted.create_project(
            {
                "request_key": "legacy-create",
                "name": "Ship the slice",
                "objective": "Close one independently verifiable vertical slice",
                "closed_when": ["All specified acceptance tests pass"],
                "proposer_id": "planner",
                "ratifier_id": "ratifier",
                "actions": [{"title": "Implement slice", "success_criteria": ["test passes"]}],
                "max_attempts": 8,
                "max_effects": 4,
            }
        )
        self.assertEqual(replay["id"], project["id"])
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            effect = conn.execute(
                "SELECT * FROM effect_reservations WHERE idempotency_key = 'legacy-effect'"
            ).fetchone()
            indexes = {row["name"] for row in conn.execute("PRAGMA index_list(effect_reservations)")}
            version = conn.execute(
                "SELECT version FROM closure_schema_meta WHERE singleton = 1"
            ).fetchone()[0]
        self.assertEqual(effect["status"], "UNKNOWN")
        self.assertEqual(effect["attempt_key"], "legacy-claim")
        self.assertIn("idx_effect_attempt_digest", indexes)
        self.assertNotIn("idx_effect_action_digest", indexes)
        self.assertEqual(version, 2)
        reconciled = restarted.reconcile_unknown_effect(
            "legacy-effect",
            {
                "operator_id": "migration-operator",
                "authorization_ref": "approval://legacy-reconciliation",
                "decision": "FAILED",
                "result_digest": "f" * 64,
                "evidence": evidence("legacy-reconciled"),
            },
        )
        self.assertEqual(reconciled["reservation"]["status"], "FAILED")
        self.assertTrue(restarted.health()["layers"]["control"]["materialization_ok"])
        self.assertEqual(ClosureStore(self.path).get_project(project["id"])["id"], project["id"])

    def test_four_layer_health_is_healthy_but_conformance_stays_unscored(self):
        self.create_project()
        with patch.dict(os.environ, {}, clear=True):
            health = self.store.health()
        self.assertEqual(health["status"], "ok")
        self.assertEqual(set(health["layers"]), {"mechanism", "control", "workflow", "outcome"})
        self.assertEqual(health["conformance"], "UNSCORED")

    def test_health_reports_incomplete_remote_authority_configuration(self):
        self.create_project()
        with patch.dict(
            os.environ, {"FLOYD_CLOSURE_TOKEN": "perimeter-only"}, clear=True
        ):
            health = self.store.health()
        authority = health["layers"]["control"]["authority"]
        self.assertEqual(health["status"], "degraded")
        self.assertFalse(authority["ok"])
        self.assertEqual(authority["reason"], "remote_mode_requires_principal_registry")

    def test_health_reports_missing_remote_role_coverage(self):
        self.create_project()
        principals = json.dumps(
            {"only-worker": {"token": "worker-only", "roles": ["worker"]}}
        )
        with patch.dict(
            os.environ,
            {
                "FLOYD_CLOSURE_TOKEN": "perimeter",
                "FLOYD_CLOSURE_PRINCIPALS_JSON": principals,
            },
            clear=True,
        ):
            health = self.store.health()
        authority = health["layers"]["control"]["authority"]
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(authority["reason"], "missing_required_roles")
        self.assertIn("verifier", authority["missing_roles"])
        self.assertIn("proposer", authority["missing_roles"])

    def test_health_requires_distinct_approval_authority_for_open_approval_work(self):
        self.create_project(
            actions=[
                {
                    "title": "Publish",
                    "success_criteria": ["release is visible"],
                    "risk": "irreversible",
                }
            ]
        )
        with patch.dict(os.environ, {}, clear=True):
            health = self.store.health()
        authority = health["layers"]["control"]["authority"]
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(authority["reason"], "open_approval_actions_require_approval_credential")


if __name__ == "__main__":
    unittest.main()
