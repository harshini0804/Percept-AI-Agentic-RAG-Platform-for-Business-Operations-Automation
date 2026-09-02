"""
API tests using FastAPI's TestClient (Section 5's shared UI screens'
backing endpoints). Runs against the real test database.

The submissions tests mock call_llm (same pattern as
test_dummy_vertical_integration.py) but still exercise the real
embedding model via the real embed_node — so, like that file, they
need network access to huggingface.co on first run in a given
environment (cached afterward).
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.logging_service import (
    create_agent_run,
    complete_agent_run,
    create_escalation,
    create_notification,
)

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------
# /agent-runs (GET)
# ---------------------------------------------------------------

def test_list_agent_runs_empty():
    response = client.get("/agent-runs")
    assert response.status_code == 200
    assert response.json() == []


def test_list_and_get_agent_run():
    run_id = create_agent_run(vertical="dummy", trigger_type="upload")
    complete_agent_run(run_id, status="completed", confidence=0.88)

    list_response = client.get("/agent-runs")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert list_response.json()[0]["id"] == run_id

    detail_response = client.get(f"/agent-runs/{run_id}")
    assert detail_response.status_code == 200
    body = detail_response.json()
    assert body["status"] == "completed"
    assert body["confidence"] == pytest.approx(0.88)
    assert body["decisions"] == []


def test_get_nonexistent_agent_run_returns_404():
    import uuid

    response = client.get(f"/agent-runs/{uuid.uuid4()}")
    assert response.status_code == 404


def test_list_agent_runs_filters_by_vertical():
    create_agent_run(vertical="dummy", trigger_type="upload")
    create_agent_run(vertical="post_incident", trigger_type="upload")

    response = client.get("/agent-runs?vertical=post_incident")
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["vertical"] == "post_incident"


# ---------------------------------------------------------------
# /escalations
# ---------------------------------------------------------------

def test_list_escalations_defaults_to_open_only():
    run_id = create_agent_run(vertical="dummy", trigger_type="upload")
    create_escalation(run_id, reason="needs review")

    response = client.get("/escalations")
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["status"] == "open"
    assert results[0]["vertical"] == "dummy"


def test_resolve_escalation_approve_fires_pending_action():
    run_id = create_agent_run(vertical="dummy", trigger_type="upload")
    escalation_id = create_escalation(
        run_id,
        reason="low confidence",
        pending_action={"tool_name": "log_dummy_action", "arguments": {"note": "approved case"}},
    )

    response = client.post(
        f"/escalations/{escalation_id}/resolve",
        json={"approve": True, "resolved_by": "reviewer"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"

    run_response = client.get(f"/agent-runs/{run_id}")
    assert run_response.json()["status"] == "completed"
    # Regression guard: approving must NOT reset the run's original confidence.
    detail_response = client.get(f"/agent-runs/{run_id}")
    assert "decisions" in detail_response.json()
    action_decisions = [
        d for d in detail_response.json()["decisions"] if d["step_type"] == "action"
    ]
    assert len(action_decisions) == 1
    assert action_decisions[0]["detail"]["approved_by_human"] is True


def test_resolve_escalation_reject_does_not_fire_action():
    run_id = create_agent_run(vertical="dummy", trigger_type="upload")
    escalation_id = create_escalation(
        run_id,
        reason="low confidence",
        pending_action={"tool_name": "log_dummy_action", "arguments": {"note": "should not fire"}},
    )

    response = client.post(
        f"/escalations/{escalation_id}/resolve", json={"approve": False}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    detail_response = client.get(f"/agent-runs/{run_id}")
    action_decisions = [
        d for d in detail_response.json()["decisions"] if d["step_type"] == "action"
    ]
    assert len(action_decisions) == 0



def test_resolve_escalation_reject_closes_out_the_run_status():
    """
    Phase F, item F1 (fixed): rejecting an escalation must close out
    the underlying run, not leave it stuck at 'escalated' forever.
    """
    run_id = create_agent_run(vertical="dummy", trigger_type="upload")
    # Mirrors what action_gate_node actually does when it escalates —
    # sets the run itself to 'escalated', not just the escalation row.
    complete_agent_run(run_id, status="escalated", confidence=0.4)
    escalation_id = create_escalation(run_id, reason="low confidence")

    client.post(f"/escalations/{escalation_id}/resolve", json={"approve": False})

    run_response = client.get(f"/agent-runs/{run_id}")
    assert run_response.json()["status"] == "rejected"


def test_resolve_escalation_approve_without_pending_action_returns_400():
    run_id = create_agent_run(vertical="dummy", trigger_type="upload")
    escalation_id = create_escalation(run_id, reason="nothing to do here")

    response = client.post(
        f"/escalations/{escalation_id}/resolve", json={"approve": True}
    )
    assert response.status_code == 400


def test_resolve_already_resolved_escalation_returns_400():
    run_id = create_agent_run(vertical="dummy", trigger_type="upload")
    escalation_id = create_escalation(run_id, reason="x")
    client.post(f"/escalations/{escalation_id}/resolve", json={"approve": False})

    second_attempt = client.post(
        f"/escalations/{escalation_id}/resolve", json={"approve": False}
    )
    assert second_attempt.status_code == 400


def test_resolve_nonexistent_escalation_returns_404():
    import uuid

    response = client.post(
        f"/escalations/{uuid.uuid4()}/resolve", json={"approve": True}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------
# /notifications
# ---------------------------------------------------------------

def test_list_and_mark_read_notification():
    run_id = create_agent_run(vertical="dummy", trigger_type="upload")
    notification_id = create_notification(run_id, recipient="a@example.com", message="hi")

    unread_response = client.get("/notifications?unread_only=true")
    assert len(unread_response.json()) == 1

    mark_response = client.post(f"/notifications/{notification_id}/mark-read")
    assert mark_response.status_code == 200
    assert mark_response.json()["read"] is True

    unread_after = client.get("/notifications?unread_only=true")
    assert len(unread_after.json()) == 0


def test_mark_read_nonexistent_notification_returns_404():
    import uuid

    response = client.post(f"/notifications/{uuid.uuid4()}/mark-read")
    assert response.status_code == 404


# ---------------------------------------------------------------
# /admin
# ---------------------------------------------------------------

def test_resync_unknown_vertical_returns_400():
    response = client.post("/admin/resync/not_a_real_vertical")
    assert response.status_code == 400


def test_sync_history_empty_is_ok():
    response = client.get("/admin/sync-history")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------
# /evaluation
# ---------------------------------------------------------------

def test_evaluation_metrics_empty_is_ok():
    response = client.get("/evaluation/metrics")
    assert response.status_code == 200
    assert response.json() == []


def test_evaluation_metrics_shape_after_runs_exist():
    run_id = create_agent_run(vertical="dummy", trigger_type="upload")
    complete_agent_run(run_id, status="completed", confidence=0.9)

    response = client.get("/evaluation/metrics")
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    m = results[0]
    assert m["vertical"] == "dummy"
    assert m["total_runs"] == 1
    assert m["completed_count"] == 1
    assert m["resolution_rate"] == pytest.approx(1.0)


# ---------------------------------------------------------------
# /agent-runs (POST) — submissions, via the vertical registry
# ---------------------------------------------------------------

def test_submit_run_unregistered_vertical_returns_404():
    response = client.post(
        "/agent-runs", json={"vertical": "not_registered", "input_text": "x"}
    )
    assert response.status_code == 404


def test_submit_run_valid_vertical_but_not_yet_built_returns_404():
    """
    Distinct from the above: 'post_incident' IS a real, valid
    Vertical enum member (Section 4.3) but has no vertical.graph.py
    registered yet since no real vertical has been built. This should
    still 404, via a different code path (the registry lookup, not
    enum validation) than a nonsense vertical name.
    """
    response = client.post(
        "/agent-runs", json={"vertical": "post_incident", "input_text": "x"}
    )
    assert response.status_code == 404


def test_submit_run_dummy_vertical_end_to_end(monkeypatch):
    """
    Needs network access to huggingface.co on first run in a fresh
    environment (real embed_node inside the real dummy vertical
    graph) — only call_llm is mocked.
    """
    monkeypatch.setattr(
        "app.core.orchestration.call_llm",
        lambda messages, tools=None: {
            "content": '{"confidence": 0.95, "should_act": true, "reason": "test"}',
            "tool_calls": [],
        },
    )

    response = client.post(
        "/agent-runs",
        json={"vertical": "dummy", "input_text": "the server crashed"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["confidence"] == pytest.approx(0.95)
    assert body["escalated"] is False
    # Phase B: confirms the reconciled contract's actions_taken field
    # is genuinely populated, not just present-but-empty.
    assert len(body["actions_taken"]) == 1
    assert body["actions_taken"][0]["action_name"] == "log_dummy_action"
    assert body["escalation_reason"] is None


def test_submit_run_with_file_upload_end_to_end(monkeypatch):
    """
    Phase C: the file-upload variant. Needs network access to
    huggingface.co on first run (same as the text-only submission
    test above) — only call_llm is mocked.
    """
    monkeypatch.setattr(
        "app.core.orchestration.call_llm",
        lambda messages, tools=None: {
            "content": '{"confidence": 0.93, "should_act": true, "reason": "test"}',
            "tool_calls": [],
        },
    )

    file_content = b"Summary: The server crashed due to a memory leak."
    response = client.post(
        "/agent-runs/upload",
        data={"vertical": "dummy"},
        files={"file": ("postmortem.txt", file_content, "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["confidence"] == pytest.approx(0.93)


def test_submit_run_with_file_upload_unregistered_vertical_returns_404():
    response = client.post(
        "/agent-runs/upload",
        data={"vertical": "not_registered"},
        files={"file": ("test.txt", b"some content", "text/plain")},
    )
    assert response.status_code == 404


def test_submit_run_with_file_upload_unsupported_file_type_returns_400():
    response = client.post(
        "/agent-runs/upload",
        data={"vertical": "dummy"},
        files={"file": ("image.png", b"\x89PNG...", "image/png")},
    )
    assert response.status_code == 400