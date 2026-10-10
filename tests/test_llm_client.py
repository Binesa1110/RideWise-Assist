import json
from types import SimpleNamespace

import llm_client
from config import get_llm_config


def test_prepare_reply_uses_sdk_and_returns_actual_completion(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create_completion)
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def create_completion(**kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="The actual completion.")
                    )
                ]
            )

    monkeypatch.setattr(llm_client, "OpenAI", FakeClient)
    monkeypatch.setattr(llm_client, "is_llm_configured", lambda: True)
    monkeypatch.setattr(
        llm_client,
        "get_llm_config",
        lambda: {
            "base_url": "https://neo-hackathon.services.ai.azure.com/openai/v1/",
            "api_key": "test-key",
            "model": "Neo-Hackathon",
            "timeout": 30,
        },
    )

    result = llm_client.prepare_reply("A question", "Verified context")

    assert result == "The actual completion."
    assert captured["client"]["base_url"] == (
        "https://neo-hackathon.services.ai.azure.com/openai/v1/"
    )
    assert captured["request"]["model"] == "Neo-Hackathon"
    assert captured["request"]["messages"][1]["content"].endswith(
        "Verified context"
    )


def test_full_completions_url_is_normalized_to_sdk_base_url(monkeypatch):
    monkeypatch.setenv(
        "NEOSTATS_API_URL",
        "https://neo-hackathon.services.ai.azure.com/openai/v1/chat/completions",
    )
    monkeypatch.setenv("NEOSTATS_API_KEY", "test-key")
    monkeypatch.setenv("NEOSTATS_MODEL", "Neo-Hackathon")

    assert get_llm_config()["base_url"] == (
        "https://neo-hackathon.services.ai.azure.com/openai/v1"
    )


def test_agent_turn_sends_tool_definitions_to_neo_stats(monkeypatch):
    captured = {}
    tool_definitions = [{"type": "function", "function": {"name": "lookup_booking"}}]
    expected_message = SimpleNamespace(content=None, tool_calls=["mock-call"])

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create_completion)
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def create_completion(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=expected_message)])

    monkeypatch.setattr(llm_client, "OpenAI", FakeClient)
    monkeypatch.setattr(llm_client, "is_llm_configured", lambda: True)
    monkeypatch.setattr(
        llm_client,
        "get_llm_config",
        lambda: {
            "base_url": "https://neo-hackathon.services.ai.azure.com/openai/v1/",
            "api_key": "test-key",
            "model": "Neo-Hackathon",
            "timeout": 30,
        },
    )

    response = llm_client.complete_agent_turn(
        [{"role": "user", "content": "Look up booking"}],
        tool_definitions,
    )

    assert response is expected_message
    assert captured["tools"] == tool_definitions
    assert captured["tool_choice"] == "auto"
    assert captured["model"] == "Neo-Hackathon"


def test_memory_extraction_uses_configured_sdk_and_parses_json(monkeypatch):
    captured = {}
    candidate = {
        "issue_category": "booking",
        "issue_keywords": ["booking", "status"],
        "resolution_keywords": [],
    }

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create_completion)
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def create_completion(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(candidate))
                    )
                ]
            )

    monkeypatch.setattr(llm_client, "OpenAI", FakeClient)
    monkeypatch.setattr(llm_client, "is_llm_configured", lambda: True)
    monkeypatch.setattr(
        llm_client,
        "get_llm_config",
        lambda: {
            "base_url": "https://neo-hackathon.services.ai.azure.com/openai/v1/",
            "api_key": "test-key",
            "model": "Neo-Hackathon",
            "timeout": 30,
        },
    )

    result = llm_client.extract_memory_candidate(
        "My booking status is wrong", "I could not verify status.", []
    )

    assert result == candidate
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["model"] == "Neo-Hackathon"


def test_memory_extraction_unwraps_provider_json_envelope(monkeypatch):
    candidate = {
        "issue_category": "booking",
        "issue_keywords": ["booking", "status"],
        "resolution_keywords": [],
    }

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_request: SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content=json.dumps({"final{": candidate})
                                )
                            )
                        ]
                    )
                )
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(llm_client, "OpenAI", FakeClient)
    monkeypatch.setattr(llm_client, "is_llm_configured", lambda: True)
    monkeypatch.setattr(
        llm_client,
        "get_llm_config",
        lambda: {
            "base_url": "https://neo-hackathon.services.ai.azure.com/openai/v1/",
            "api_key": "test-key",
            "model": "Neo-Hackathon",
            "timeout": 30,
        },
    )

    assert llm_client.extract_memory_candidate("question", "answer", []) == candidate
