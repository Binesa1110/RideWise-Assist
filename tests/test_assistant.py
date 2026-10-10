from assistant import RideWiseAssistant


def test_general_question_returns_agent_final_answer(monkeypatch):
    from types import SimpleNamespace

    import assistant

    monkeypatch.setattr(assistant, "is_llm_configured", lambda: True)
    monkeypatch.setattr(
        assistant,
        "complete_agent_turn",
        lambda _messages, _tools: SimpleNamespace(
            tool_calls=None, content="Actual NeoStats answer."
        ),
    )

    result = RideWiseAssistant(
        user=assistant.DEFAULT_ACCESS_CONTROL.login("customer", "customer")
    ).answer_question("Hello, can you help?")

    assert result["answer"] == "Actual NeoStats answer."
    assert result["intent"] == "general"


def test_empty_question_is_rejected():
    result = RideWiseAssistant().answer_question("   ")
    assert result["intent"] == "invalid"
    assert "Please provide" in result["answer"]


def test_missing_policy_is_reported_cleanly():
    result = RideWiseAssistant().answer_question("What is the cancellation refund policy?")
    assert result["status"] in {"llm_missing", "unauthorized"}


def test_assistant_handles_missing_llm_without_crashing():
    result = RideWiseAssistant().answer_question("How does the EV battery fee work?")
    assert result["intent"] in {"battery", "general"}
