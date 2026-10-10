from datetime import datetime, timezone
import base64
import re
import zlib
from chat_export import (
    get_user_conversation,
    render_chat_pdf,
    render_chat_txt,
)
from access_control import DevelopmentUser


def test_empty_conversation_exports_a_clear_message():
    exported = render_chat_txt([], datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert "No conversation messages to export." in exported
    assert "Exported: 2026-01-01T00:00:00+00:00" in exported


def test_txt_export_includes_only_conversation_and_timestamps():
    conversation = [
        {
            "role": "user",
            "content": "Where is my booking?",
            "timestamp": "2026-01-01T09:00:00+00:00",
        },
        {
            "role": "assistant",
            "content": "Please provide the booking reference.",
            "timestamp": "2026-01-01T09:00:02+00:00",
        },
        {"role": "system", "content": "Do not export this prompt."},
        {"role": "assistant", "content": "Internal debug output.", "debug": True},
    ]

    exported = render_chat_txt(conversation)

    assert "Where is my booking?" in exported
    assert "Please provide the booking reference." in exported
    assert "2026-01-01T09:00:00+00:00" in exported
    assert "Do not export this prompt." not in exported
    assert "Internal debug output." not in exported


def test_pdf_export_generates_readable_pdf_with_page_number():
    pdf = render_chat_pdf(
        [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]
    )

    assert pdf.startswith(b"%PDF-")
    assert b"RideWise Assist Conversation" in pdf
    streams = re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.DOTALL)
    decoded_streams = []
    for stream in streams:
        try:
            decoded_streams.append(zlib.decompress(base64.a85decode(stream, adobe=True)))
        except (ValueError, zlib.error):
            continue
    assert any(b"Page 1 of 1" in stream for stream in decoded_streams)


def test_conversations_are_isolated_by_current_user():
    state = {}
    customer = DevelopmentUser(1, "customer", "customer")
    support = DevelopmentUser(2, "support", "support")

    customer_messages = get_user_conversation(state, customer)
    customer_messages.append({"role": "user", "content": "Customer-only"})
    support_messages = get_user_conversation(state, support)

    assert support_messages == []
    assert get_user_conversation(state, customer) == [
        {"role": "user", "content": "Customer-only"}
    ]


def test_export_redacts_credentials_and_skips_system_messages():
    exported = render_chat_txt(
        [
            {
                "role": "assistant",
                "content": (
                    "NEOSTATS_API_KEY=topsecret sk-1234567890abcdefghijkl "
                    "NEOSTATS_MODEL=Neo-Hackathon Bearer token-value"
                ),
            },
            {"role": "system", "content": "Never export this."},
        ]
    )

    assert "topsecret" not in exported
    assert "sk-1234567890abcdefghijkl" not in exported
    assert "Neo-Hackathon" not in exported
    assert "token-value" not in exported
    assert "Never export this." not in exported
