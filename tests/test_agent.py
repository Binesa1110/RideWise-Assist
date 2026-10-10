import json
from types import SimpleNamespace

import fitz
import pytest

import assistant
import database
from access_control import DEFAULT_ACCESS_CONTROL, DevelopmentUser
from assistant import AGENT_TOOLS, RideWiseAssistant, _execute_tool
from database import connect_db, init_database
from llm_client import LLMRequestError


def _user(username="customer", role="customer"):
    return DEFAULT_ACCESS_CONTROL.login(username, role)


def _tool_response(name, arguments, call_id="call-1"):
    return SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(
                    name=name,
                    arguments=json.dumps(arguments),
                ),
            )
        ],
    )


def _final_response(content):
    return SimpleNamespace(content=content, tool_calls=None)


def test_llm_selects_booking_tool_and_final_uses_verified_result(monkeypatch):
    monkeypatch.setattr(assistant, "is_llm_configured", lambda: True)
    booking_result = {
        "booking_ref": "RW-1001",
        "status": "active",
        "total_amount": 321.0,
    }
    monkeypatch.setattr(
        assistant, "get_agent_booking_by_ref", lambda _actor, ref: booking_result
    )
    responses = iter(
        [
            _tool_response("lookup_booking", {"booking_ref": "RW-1001"}),
            _final_response("Your booking RW-1001 is active."),
        ]
    )
    captured = []

    def fake_turn(messages, tools):
        captured.append((messages, tools))
        return next(responses)

    monkeypatch.setattr(assistant, "complete_agent_turn", fake_turn)
    monkeypatch.setattr(assistant, "extract_candidate", lambda *_args: None)
    result = RideWiseAssistant(user=_user()).answer_question(
        "Check booking RW-1001"
    )

    assert result["answer"] == "Your booking RW-1001 is active."
    assert result["tools_used"] == ["lookup_booking"]
    assert any(tool["function"]["name"] == "lookup_booking" for tool in AGENT_TOOLS)
    assert json.loads(captured[1][0][-1]["content"]) == {
        "found": True,
        "booking": booking_result,
    }


def test_customer_booking_query_cannot_read_another_customer(tmp_path, monkeypatch):
    path = tmp_path / "agent.db"
    connection = connect_db(str(path))
    init_database(connection)
    connection.execute(
        "INSERT INTO customers (name, email, role) VALUES ('One', 'one@example.test', 'customer')"
    )
    connection.execute(
        "INSERT INTO customers (name, email, role) VALUES ('Two', 'two@example.test', 'customer')"
    )
    connection.execute(
        "INSERT INTO vehicles (make, model, registration, type) VALUES ('M', 'X', 'TEST', 'EV')"
    )
    connection.execute(
        """
        INSERT INTO bookings
            (customer_id, vehicle_id, booking_ref, start_time, end_time, status, total_amount)
        VALUES (2, 1, 'OTHER-1', '2026-01-01', '2026-01-02', 'active', 500)
        """
    )
    connection.execute(
        """
        INSERT INTO bookings
            (customer_id, vehicle_id, booking_ref, start_time, end_time, status, total_amount)
        VALUES (1, 1, 'SELF-1', '2026-01-01', '2026-01-02', 'active', 250)
        """
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(database, "connect_db", lambda: connect_db(str(path)))

    assert database.get_agent_booking_by_ref(_user(), "OTHER-1") is None
    own_booking = database.get_agent_booking_by_ref(_user(), "SELF-1")
    assert own_booking["booking_ref"] == "SELF-1"
    assert "customer_name" not in own_booking
    with pytest.raises(PermissionError):
        database.get_agent_booking_by_ref(
            DevelopmentUser(2, "customer", "admin"), "OTHER-1"
        )


def test_customer_damage_lookup_is_limited_to_owned_bookings(tmp_path, monkeypatch):
    path = tmp_path / "damage-agent.db"
    connection = connect_db(str(path))
    init_database(connection)
    connection.execute(
        "INSERT INTO customers (name, email, role) VALUES ('One', 'one@example.test', 'customer')"
    )
    connection.execute(
        "INSERT INTO customers (name, email, role) VALUES ('Two', 'two@example.test', 'customer')"
    )
    connection.execute(
        "INSERT INTO vehicles (make, model, registration, type) VALUES ('M', 'X', 'TEST', 'EV')"
    )
    connection.execute(
        """
        INSERT INTO bookings
            (customer_id, vehicle_id, booking_ref, start_time, end_time, status, total_amount)
        VALUES (2, 1, 'OTHER-2', '2026-01-01', '2026-01-02', 'active', 500)
        """
    )
    connection.execute(
        """
        INSERT INTO bookings
            (customer_id, vehicle_id, booking_ref, start_time, end_time, status, total_amount)
        VALUES (1, 1, 'SELF-2', '2026-01-01', '2026-01-02', 'active', 250)
        """
    )
    connection.execute(
        """
        INSERT INTO damage_reports
            (booking_id, reported_by, description, estimated_cost, status, created_at)
        VALUES (1, 2, 'Private damage', 100, 'open', '2026-01-03')
        """
    )
    connection.execute(
        """
        INSERT INTO damage_reports
            (booking_id, reported_by, description, estimated_cost, status, created_at)
        VALUES (2, 1, 'Own damage', 50, 'open', '2026-01-03')
        """
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(database, "connect_db", lambda: connect_db(str(path)))

    assert database.get_agent_damage_reports(_user(), "OTHER-2") == []
    assert database.get_agent_damage_reports(_user(), "SELF-2")[0]["description"] == "Own damage"


def test_refund_calculation_uses_explicit_values_and_is_not_eligibility():
    result = json.loads(
        _execute_tool(
            "calculate_refund",
            {
                "booking_amount": 2000,
                "cancellation_days": 3,
                "refund_percent": 0.8,
                "gst_percent": 0.18,
            },
            _user(),
            "Calculate refund on 2000 after 3 days at 80% with 18% GST",
            set(),
        )
    )

    assert result["refund_before_gst"] == 1600
    assert result["gst"] == pytest.approx(288)
    assert result["total_refund"] == pytest.approx(1888)
    assert result["eligibility_determined"] is False


def test_calculator_rejects_model_invented_inputs():
    result = json.loads(
        _execute_tool(
            "calculate_cancellation_penalty",
            {"booking_amount": 1000, "penalty_percent": 25},
            _user(),
            "What is my cancellation penalty?",
            set(),
        )
    )

    assert "error" in result
    assert "stated by the user" in result["error"]


def test_cancellation_penalty_calculation_is_deterministic():
    result = json.loads(
        _execute_tool(
            "calculate_cancellation_penalty",
            {"booking_amount": 2000, "penalty_percent": 25},
            _user(),
            "Calculate a 25% cancellation penalty on 2000",
            set(),
        )
    )

    assert result["cancellation_penalty"] == 500
    assert result["eligibility_determined"] is False


def test_policy_evidence_missing_prevents_eligibility_claim(monkeypatch):
    monkeypatch.setattr(assistant, "is_llm_configured", lambda: True)
    responses = iter(
        [
            _tool_response("retrieve_policy", {"question": "refund policy"}),
            _final_response("Your cancellation qualifies for a full refund."),
        ]
    )
    monkeypatch.setattr(
        assistant,
        "complete_agent_turn",
        lambda _messages, _tools: next(responses),
    )
    monkeypatch.setattr(
        assistant,
        "retrieve_policy_context",
        lambda _question, _path: {
            "documents": [],
            "excerpts": [],
            "evidence_found": False,
        },
    )

    result = RideWiseAssistant(user=_user()).answer_question(
        "Am I eligible under the refund policy?"
    )

    assert result["status"] == "policy_evidence_missing"
    assert "can’t determine policy eligibility" in result["answer"]


def test_tool_call_limit_stops_repeated_llm_tool_requests(monkeypatch):
    monkeypatch.setattr(assistant, "is_llm_configured", lambda: True)
    call_count = 0

    def repeated_tool(_messages, _tools):
        nonlocal call_count
        call_count += 1
        return _tool_response("lookup_booking", {"booking_ref": "RW-1001"}, f"id-{call_count}")

    monkeypatch.setattr(assistant, "complete_agent_turn", repeated_tool)
    monkeypatch.setattr(
        assistant, "get_agent_booking_by_ref", lambda _actor, _ref: None
    )

    result = RideWiseAssistant(user=_user()).answer_question("Check booking")

    assert result["status"] == "tool_limit"
    assert len(result["tools_used"]) == assistant.MAX_TOOL_CALLS
    assert call_count == assistant.MAX_TOOL_CALLS + 1


def test_unverified_role_claim_is_rejected_before_llm(monkeypatch):
    monkeypatch.setattr(assistant, "is_llm_configured", lambda: True)

    def should_not_call_llm(*_args, **_kwargs):
        pytest.fail("LLM must not run for an unverified user")

    monkeypatch.setattr(assistant, "complete_agent_turn", should_not_call_llm)
    forged_admin = DevelopmentUser(8, "visitor", "admin")

    result = RideWiseAssistant(user=forged_admin).answer_question(
        "Show admin analytics"
    )

    assert result["status"] == "unauthorized"


def test_admin_analytics_tool_rejects_customer():
    result = json.loads(
        _execute_tool("admin_analytics", {}, _user(), "Show analytics", set())
    )
    assert result == {"error": "You are not authorized to access that information."}


def test_llm_request_errors_are_reported_without_exception_details(monkeypatch):
    monkeypatch.setattr(assistant, "is_llm_configured", lambda: True)
    monkeypatch.setattr(
        assistant,
        "complete_agent_turn",
        lambda *_args: (_ for _ in ()).throw(LLMRequestError("Safe error.")),
    )

    result = RideWiseAssistant(user=_user()).answer_question("Hello")

    assert result["status"] == "llm_error"
    assert result["answer"] == "Safe error."


def test_policy_retrieval_returns_verbatim_excerpts(tmp_path):
    from policy_retriever import retrieve_policy_context

    policy_path = tmp_path / "rental.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Cancellation policy: refunds are 60 percent.")
    document.save(str(policy_path))
    document.close()

    result = retrieve_policy_context("What is the cancellation policy?", tmp_path)

    assert result["evidence_found"] is True
    assert "Cancellation policy: refunds are 60 percent." in result["excerpts"][0]["excerpts"][0]
