import json

import pytest

import assistant
import customer_memory
import llm_client
from access_control import DEFAULT_ACCESS_CONTROL, DevelopmentUser
from database import connect_db, init_database
from llm_client import LLMRequestError


def _customer():
    return DEFAULT_ACCESS_CONTROL.login("customer", "customer")


def _admin():
    return DEFAULT_ACCESS_CONTROL.login("admin", "admin")


def _memory_db(path):
    connection = connect_db(str(path))
    init_database(connection)
    connection.execute(
        "INSERT INTO customers (id, name, email, role) VALUES (1, 'Customer', 'c@example.test', 'customer')"
    )
    connection.execute(
        "INSERT INTO customers (id, name, email, role) VALUES (2, 'Support', 's@example.test', 'support')"
    )
    connection.commit()
    connection.close()
    return str(path)


def _candidate(
    category="booking",
    issue_keywords=None,
    resolution_keywords=None,
):
    return {
        "issue_category": category,
        "issue_keywords": issue_keywords or ["booking", "status"],
        "resolution_keywords": resolution_keywords or [],
    }


def test_save_customer_memory_and_deduplicate(tmp_path):
    db_path = _memory_db(tmp_path / "memory.db")
    question = "My booking status is wrong"

    first_id = customer_memory.save_customer_memory(
        _customer(), _candidate(), question, db_path=db_path
    )
    second_id = customer_memory.save_customer_memory(
        _customer(), _candidate(), question, db_path=db_path
    )

    assert first_id == second_id
    connection = connect_db(db_path)
    row = connection.execute(
        """
        SELECT user_id, issue_category, issue_keywords, resolution_keywords,
               issue_status, created_at, updated_at
        FROM customer_memory WHERE memory_id = ?
        """,
        (first_id,),
    ).fetchone()
    connection.close()
    assert row["user_id"] == 1
    assert row["issue_category"] == "booking"
    assert json.loads(row["issue_keywords"]) == ["booking", "status"]
    assert json.loads(row["resolution_keywords"]) == []
    assert row["issue_status"] == "open"


def test_resolution_is_saved_only_when_tool_evidence_supports_keywords(tmp_path):
    db_path = _memory_db(tmp_path / "resolution.db")
    question = "My payment is pending"
    candidate = _candidate("payment", ["payment", "pending"], ["paid"])

    memory_id = customer_memory.save_customer_memory(
        _customer(),
        candidate,
        question,
        tool_evidence=['{"status":"paid"}'],
        db_path=db_path,
    )

    connection = connect_db(db_path)
    row = connection.execute(
        "SELECT resolution_keywords, issue_status FROM customer_memory WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    connection.close()
    assert json.loads(row["resolution_keywords"]) == ["paid"]
    assert row["issue_status"] == "open"


def test_retrieval_matches_only_own_memory(tmp_path):
    db_path = _memory_db(tmp_path / "retrieval.db")
    customer_memory.save_customer_memory(
        _customer(),
        _candidate(),
        "My booking status is wrong",
        db_path=db_path,
    )
    connection = connect_db(db_path)
    connection.execute(
        """
        INSERT INTO customer_memory
            (user_id, issue_category, issue_keywords, resolution_keywords,
             issue_status, created_at, updated_at)
        VALUES (2, 'payment', '[\"payment\",\"refund\"]', '[]', 'open', '2026', '2026')
        """
    )
    connection.commit()
    connection.close()

    result = customer_memory.retrieve_customer_memory(
        _customer(), "What happened to my booking before?", db_path=db_path
    )

    assert len(result["records"]) == 1
    assert result["records"][0]["issue_category"] == "booking"
    assert result["records"][0]["issue_keywords"] == ["booking", "status"]


def test_cross_customer_and_unverified_role_access_are_denied(tmp_path):
    db_path = _memory_db(tmp_path / "auth.db")
    customer_memory.save_customer_memory(
        _customer(),
        _candidate(),
        "My booking status is wrong",
        db_path=db_path,
    )

    with pytest.raises(PermissionError):
        customer_memory.retrieve_customer_memory(
            DevelopmentUser(2, "customer", "customer"),
            "booking",
            db_path=db_path,
        )
    with pytest.raises(PermissionError):
        customer_memory.list_admin_customer_memories(
            DevelopmentUser(1, "customer", "admin"),
            db_path=db_path,
        )


def test_empty_memory_returns_no_match(tmp_path):
    db_path = _memory_db(tmp_path / "empty-memory.db")

    result = customer_memory.retrieve_customer_memory(
        _customer(), "What happened to my previous booking?", db_path=db_path
    )

    assert result == {"records": [], "ambiguous": False}


def test_previous_issue_question_reports_empty_memory_without_llm(tmp_path, monkeypatch):
    db_path = _memory_db(tmp_path / "empty-query.db")
    monkeypatch.setattr(
        customer_memory, "connect_db", lambda _path=None: connect_db(db_path)
    )
    monkeypatch.setattr(assistant, "is_llm_configured", lambda: True)
    monkeypatch.setattr(
        assistant,
        "complete_agent_turn",
        lambda *_args: pytest.fail("No-match memory queries should not call the LLM"),
    )

    result = assistant.RideWiseAssistant(user=_customer()).answer_question(
        "What happened to my previous booking?"
    )

    assert result["status"] == "previous_issue_not_found"
    assert "couldn't find a matching previous issue" in result["answer"]


def test_invalid_llm_candidate_is_not_saved(tmp_path):
    db_path = _memory_db(tmp_path / "invalid.db")
    invalid = _candidate(
        category="invented_category",
        issue_keywords=["booking", "status"],
    )

    assert (
        customer_memory.save_customer_memory(
            _customer(),
            invalid,
            "My booking status is wrong",
            db_path=db_path,
        )
        is None
    )
    connection = connect_db(db_path)
    count = connection.execute("SELECT COUNT(*) FROM customer_memory").fetchone()[0]
    connection.close()
    assert count == 0


def test_extraction_api_failure_does_not_break_assistant(monkeypatch):
    monkeypatch.setattr(assistant, "is_llm_configured", lambda: True)
    responses = iter(
        [
            type("Response", (), {"tool_calls": None, "content": "I can help with your booking."})()
        ]
    )
    monkeypatch.setattr(
        assistant, "complete_agent_turn", lambda *_args: next(responses)
    )
    monkeypatch.setattr(
        llm_client,
        "extract_memory_candidate",
        lambda *_args: (_ for _ in ()).throw(LLMRequestError("extract failed")),
    )

    result = assistant.RideWiseAssistant(user=_customer()).answer_question(
        "My booking status is wrong"
    )

    assert result["answer"] == "I can help with your booking."
    assert result["tools_used"] == []


def test_invalid_llm_extraction_output_is_rejected(monkeypatch):
    monkeypatch.setattr(
        llm_client,
        "extract_memory_candidate",
        lambda *_args: {
            "issue_category": "invented",
            "issue_keywords": ["booking"],
            "resolution_keywords": [],
        },
    )

    assert customer_memory.extract_candidate(
        "My booking status is wrong", "I found no evidence.", []
    ) is None


def test_multiple_relevant_memories_require_clarification(tmp_path):
    db_path = _memory_db(tmp_path / "ambiguous.db")
    customer_memory.save_customer_memory(
        _customer(),
        _candidate("booking", ["booking", "status"]),
        "My booking status is wrong",
        db_path=db_path,
    )
    customer_memory.save_customer_memory(
        _customer(),
        _candidate("cancellation", ["booking", "cancellation"]),
        "My booking cancellation is wrong",
        db_path=db_path,
    )

    result = customer_memory.retrieve_customer_memory(
        _customer(), "What happened to my booking?", db_path=db_path
    )

    assert result["ambiguous"] is True
    assert len(result["records"]) == 2


def test_assistant_supplies_matching_memory_to_existing_agent(tmp_path, monkeypatch):
    db_path = _memory_db(tmp_path / "agent-context.db")
    customer_memory.save_customer_memory(
        _customer(),
        _candidate(),
        "My booking status is wrong",
        db_path=db_path,
    )
    monkeypatch.setattr(
        customer_memory, "connect_db", lambda _path=None: connect_db(db_path)
    )
    monkeypatch.setattr(assistant, "is_llm_configured", lambda: True)
    captured = {}

    def fake_turn(messages, _tools):
        captured["message"] = messages[0]["content"]
        return type("Response", (), {"tool_calls": None, "content": "You asked before about booking status."})()

    monkeypatch.setattr(assistant, "complete_agent_turn", fake_turn)
    monkeypatch.setattr(assistant, "extract_candidate", lambda *_args: None)

    result = assistant.RideWiseAssistant(user=_customer()).answer_question(
        "What happened to my booking before?"
    )

    assert "Verified saved customer-memory record" in captured["message"]
    assert "issue_keywords=booking, status" in captured["message"]
    assert result["answer"] == "You asked before about booking status."


def test_assistant_persists_validated_memory_without_changing_reply(tmp_path, monkeypatch):
    db_path = _memory_db(tmp_path / "assistant-save.db")
    monkeypatch.setattr(
        customer_memory, "connect_db", lambda _path=None: connect_db(db_path)
    )
    monkeypatch.setattr(assistant, "is_llm_configured", lambda: True)
    monkeypatch.setattr(
        assistant,
        "complete_agent_turn",
        lambda *_args: type(
            "Response", (), {"tool_calls": None, "content": "I could not verify the booking status."}
        )(),
    )
    monkeypatch.setattr(
        assistant,
        "extract_candidate",
        lambda *_args: _candidate(),
    )

    result = assistant.RideWiseAssistant(user=_customer()).answer_question(
        "My booking status is wrong"
    )

    connection = connect_db(db_path)
    count = connection.execute(
        "SELECT COUNT(*) FROM customer_memory WHERE user_id = ?", (1,)
    ).fetchone()[0]
    connection.close()
    assert result["answer"] == "I could not verify the booking status."
    assert count == 1


def test_admin_memory_listing_and_dashboard_formatting(tmp_path):
    db_path = _memory_db(tmp_path / "dashboard.db")
    customer_memory.save_customer_memory(
        _customer(),
        _candidate(resolution_keywords=["paid"]),
        "My booking status is wrong",
        tool_evidence=['{"status":"paid"}'],
        db_path=db_path,
    )

    records = customer_memory.list_admin_customer_memories(
        _admin(), search="booking", issue_category="booking", db_path=db_path
    )
    display_rows = customer_memory.format_admin_memory_rows(records)

    assert display_rows[0]["Customer ID"] == 1
    assert display_rows[0]["Issue category"] == "booking"
    assert display_rows[0]["Issue keywords"] == "booking, status"
    assert display_rows[0]["Previous resolution"] == "paid"
    assert display_rows[0]["Issue status"] == "open"
    assert "email" not in display_rows[0]
