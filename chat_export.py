from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import re
from typing import Any, MutableMapping

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
)
from reportlab.pdfgen import canvas


EMPTY_CONVERSATION = "No conversation messages to export."
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:NEOSTATS_API_KEY|OPENAI_API_KEY|API_KEY|AUTHORIZATION)"
    r"\s*[:=]\s*)(?:Bearer\s+)?[^\s,;]+"
)
_COMMON_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_ENV_ASSIGNMENT = re.compile(r"\b([A-Z][A-Z0-9_]{2,}\s*=\s*)[^\s,;]+")
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_LONG_HEX_KEY = re.compile(r"\b[A-Fa-f0-9]{32,}\b")
_TRACEBACK = re.compile(r"(?i)traceback \(most recent call last\):")


def conversation_key(user: Any) -> str:
    """Return a stable, role-scoped key for a development user conversation."""
    return f"{user.role}:{user.user_id}"


def get_user_conversation(
    session_state: MutableMapping[str, Any], user: Any
) -> list[dict[str, Any]]:
    """Get the current user's conversation without sharing another user's messages."""
    key = conversation_key(user)
    conversations = session_state.setdefault("conversations", {})
    if not isinstance(conversations, dict):
        conversations = {}
        session_state["conversations"] = conversations

    if key not in conversations:
        legacy_messages = session_state.get("messages")
        legacy_owner = session_state.get("conversation_owner")
        if (
            isinstance(legacy_messages, list)
            and legacy_messages
            and legacy_owner in (None, key)
            and not conversations
        ):
            conversations[key] = legacy_messages
        else:
            conversations[key] = []

    messages = conversations[key]
    session_state["messages"] = messages
    session_state["conversation_owner"] = key
    return messages


def _export_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    exported = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if any(message.get(flag) for flag in ("internal", "debug", "is_internal", "is_debug")):
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        if _TRACEBACK.search(content):
            continue

        safe_content = _COMMON_API_KEY.sub("[REDACTED]", content)
        safe_content = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", safe_content)
        safe_content = _ENV_ASSIGNMENT.sub(r"\1[REDACTED]", safe_content)
        safe_content = _BEARER_TOKEN.sub("Bearer [REDACTED]", safe_content)
        safe_content = _LONG_HEX_KEY.sub("[REDACTED]", safe_content)
        exported.append(
            {
                "role": role,
                "content": safe_content,
                "timestamp": str(message.get("timestamp") or ""),
            }
        )
    return exported


def _export_timestamp(value: datetime | None) -> str:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat(timespec="seconds")


def render_chat_txt(
    messages: list[dict[str, Any]], exported_at: datetime | None = None
) -> str:
    timestamp = _export_timestamp(exported_at)
    lines = ["RideWise Assist Conversation", f"Exported: {timestamp}", ""]
    exportable = _export_messages(messages)
    if not exportable:
        lines.append(EMPTY_CONVERSATION)
        return "\n".join(lines) + "\n"

    for message in exportable:
        lines.append(f"{message['role'].title()}")
        if message["timestamp"]:
            lines.append(f"Time: {message['timestamp']}")
        lines.append(message["content"])
        lines.append("")
    return "\n".join(lines)


class _NumberedCanvas(canvas.Canvas):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.setFont("Helvetica", 9)
            self.setFillColor(colors.grey)
            self.drawRightString(
                7.75 * inch, 0.45 * inch, f"Page {self._pageNumber} of {page_count}"
            )
            super().showPage()
        super().save()


def render_chat_pdf(
    messages: list[dict[str, Any]], exported_at: datetime | None = None
) -> bytes:
    from io import BytesIO

    output = BytesIO()
    doc = BaseDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="RideWise Assist Conversation",
        author="RideWise Assist",
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="conversation",
    )
    doc.addPageTemplates(PageTemplate(id="numbered", frames=frame))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ConversationTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        textColor=colors.HexColor("#1f4e79"),
        spaceAfter=10,
    )
    body_style = ParagraphStyle(
        "ConversationBody",
        parent=styles["BodyText"],
        leading=14,
        spaceAfter=7,
    )
    timestamp_style = ParagraphStyle(
        "ConversationTimestamp",
        parent=styles["BodyText"],
        fontSize=8,
        textColor=colors.grey,
        spaceAfter=4,
    )

    story = [
        Paragraph("RideWise Assist Conversation", title_style),
        Paragraph(f"Exported: {escape(_export_timestamp(exported_at))}", timestamp_style),
        Spacer(1, 0.18 * inch),
    ]
    exportable = _export_messages(messages)
    if not exportable:
        story.append(Paragraph(escape(EMPTY_CONVERSATION), body_style))
    else:
        for message in exportable:
            entry = [
                Paragraph(f"<b>{escape(message['role'].title())}</b>", body_style),
            ]
            if message["timestamp"]:
                entry.append(
                    Paragraph(f"Time: {escape(message['timestamp'])}", timestamp_style)
                )
            entry.append(Paragraph(escape(message["content"]).replace("\n", "<br/>"), body_style))
            entry.append(Spacer(1, 0.1 * inch))
            story.append(KeepTogether(entry))
    doc.build(story, canvasmaker=_NumberedCanvas)
    return output.getvalue()
