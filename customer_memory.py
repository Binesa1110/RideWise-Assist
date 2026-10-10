from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
import sqlite3
from typing import Any

from access_control import DEFAULT_ACCESS_CONTROL, DevelopmentUser
from database import connect_db


ISSUE_CATEGORIES = {
    "booking",
    "cancellation",
    "payment",
    "damage",
    "late_return",
    "vehicle",
    "policy",
    "account",
    "other",
}
ISSUE_STATUSES = {"open", "in_progress", "resolved"}
_ISSUE_KEYWORDS = {
    "booking", "status", "reference", "cancellation", "refund", "penalty",
    "payment", "charge", "invoice", "pending", "damage", "scratch", "repair",
    "late", "return", "overdue", "battery", "charging", "vehicle", "policy",
    "eligibility", "deposit", "fee", "account", "app", "support", "price",
    "wrong", "missing", "error", "failed", "delay", "issue", "problem",
}
_RESOLUTION_KEYWORDS = {
    "paid", "processed", "credited", "resolved", "closed", "repaired",
    "cancelled", "canceled", "refunded", "completed", "pending", "open",
}
_ALIASES = {
    "reservation": "booking",
    "bookings": "booking",
    "booked": "booking",
    "cancel": "cancellation",
    "cancelled": "cancellation",
    "canceled": "cancellation",
    "refunds": "refund",
    "refunded": "refund",
    "charges": "charge",
    "billing": "payment",
    "invoice": "payment",
    "invoices": "payment",
    "scratch": "damage",
    "scratches": "damage",
    "damages": "damage",
    "repair": "damage",
    "repairs": "damage",
    "overdue": "late",
    "delayed": "late",
    "returned": "return",
    "charging": "battery",
    "ev": "vehicle",
    "cars": "vehicle",
    "fees": "fee",
    "policies": "policy",
    "eligible": "eligibility",
    "login": "account",
    "signin": "account",
}
_STOP_WORDS = {
    "about", "again", "and", "are", "been", "can", "could", "did", "does",
    "for", "from", "have", "help", "how", "issue", "last", "me", "my",
    "need", "problem", "please", "previous", "same", "the", "this", "was",
    "what", "when", "where", "why", "with", "you",
}
_PRIOR_ISSUE_QUERY = re.compile(
    r"\b(previous|before|last time|earlier|same issue|same problem|"
    r"prior issue|prior problem|what happened|did we resolve|do you remember)\b",
    re.IGNORECASE,
)
_LOGGER = logging.getLogger(__name__)


def _canonical_words(value: str) -> set[str]:
    words = {
        _ALIASES.get(word.lower(), word.lower())
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{1,24}", value)
    }
    return words - _STOP_WORDS


def _verified_actor(actor: DevelopmentUser | None, *, admin: bool = False) -> DevelopmentUser:
    user = DEFAULT_ACCESS_CONTROL.resolve_verified_user(actor)
    if user is None:
        raise PermissionError("A verified application user is required.")
    if admin:
        if not DEFAULT_ACCESS_CONTROL.authorize(user, "analytics"):
            raise PermissionError("Administrator access is required.")
    elif user.role != "customer":
        raise PermissionError("Customer memory is available only to the owning customer.")
    return user


def is_previous_issue_query(query: str) -> bool:
    return isinstance(query, str) and bool(_PRIOR_ISSUE_QUERY.search(query))


def _validate_candidate(candidate: Any, question: str) -> dict[str, Any] | None:
    if not isinstance(candidate, dict) or set(candidate) != {
        "issue_category", "issue_keywords", "resolution_keywords"
    }:
        return None
    category = candidate["issue_category"]
    if not isinstance(category, str):
        return None
    category = category.strip().lower()
    if category not in ISSUE_CATEGORIES:
        return None

    question_words = _canonical_words(question)
    output = {}
    for field in ("issue_keywords", "resolution_keywords"):
        raw_words = candidate[field]
        if (
            not isinstance(raw_words, list)
            or len(raw_words) > 5
            or any(not isinstance(word, str) or len(word) > 30 for word in raw_words)
        ):
            return None
        normalized = {
            _ALIASES.get(word.strip().lower(), word.strip().lower())
            for word in raw_words
        }
        allowed_keywords = (
            _ISSUE_KEYWORDS
            if field == "issue_keywords"
            else _RESOLUTION_KEYWORDS
        )
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{1,24}", word) for word in normalized):
            return None
        if field == "issue_keywords" and not normalized.issubset(allowed_keywords):
            return None
        if field == "resolution_keywords":
            normalized &= allowed_keywords
        output[field] = sorted(normalized)

    issue_words = set(output["issue_keywords"])
    if not issue_words or not issue_words.issubset(question_words):
        return None
    category_words = {
        "booking": {"booking", "reference", "status"},
        "cancellation": {"cancellation", "refund", "penalty"},
        "payment": {"payment", "charge", "pending", "refund"},
        "damage": {"damage", "scratch", "repair"},
        "late_return": {"late", "return", "fee"},
        "vehicle": {"vehicle", "battery", "charging"},
        "policy": {"policy", "eligibility", "fee", "refund"},
        "account": {"account"},
        "other": set(),
    }[category]
    if category != "other" and not issue_words.intersection(category_words):
        return None
    return {
        "issue_category": category,
        "issue_keywords": output["issue_keywords"],
        "resolution_keywords": output["resolution_keywords"],
    }


def _status_from_evidence(tool_evidence: list[str]) -> str:
    for evidence in tool_evidence:
        try:
            value = json.loads(evidence)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            booking = value.get("booking")
            if isinstance(booking, dict) and str(booking.get("status", "")).lower() in {
                "resolved", "closed"
            }:
                return "resolved"
            reports = value.get("reports")
            if isinstance(reports, list) and any(
                isinstance(report, dict)
                and str(report.get("status", "")).lower() in {"resolved", "closed"}
                for report in reports
            ):
                return "resolved"
    return "open"


def save_customer_memory(
    actor: DevelopmentUser | None,
    candidate: Any,
    question: str,
    tool_evidence: list[str] | None = None,
    db_path: str | None = None,
) -> int | None:
    """Validate and upsert compact memory for the verified customer only."""
    user = _verified_actor(actor)
    if not isinstance(question, str) or not question.strip():
        return None
    candidate = _validate_candidate(candidate, question)
    if candidate is None:
        return None
    evidence = tool_evidence if isinstance(tool_evidence, list) else []
    evidence_words = set()
    for item in evidence:
        if isinstance(item, str):
            evidence_words.update(_canonical_words(item))
    resolution_keywords = sorted(
        set(candidate["resolution_keywords"]) & evidence_words
    )
    issue_status = _status_from_evidence(evidence)
    keywords_json = json.dumps(candidate["issue_keywords"], separators=(",", ":"))
    resolution_json = json.dumps(resolution_keywords, separators=(",", ":"))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    connection = connect_db(db_path)
    try:
        customer = connection.execute(
            "SELECT id FROM customers WHERE id = ? AND role = ?",
            (user.user_id, "customer"),
        ).fetchone()
        if customer is None:
            return None
        row = connection.execute(
            """
            SELECT memory_id, issue_keywords, resolution_keywords, issue_status
            FROM customer_memory
            WHERE user_id = ? AND issue_category = ? AND issue_keywords = ?
            """,
            (user.user_id, candidate["issue_category"], keywords_json),
        ).fetchone()
        if row:
            old_resolution = json.loads(row["resolution_keywords"])
            merged_resolution = sorted(set(old_resolution) | set(resolution_keywords))
            merged_status = (
                "resolved"
                if row["issue_status"] == "resolved" or issue_status == "resolved"
                else row["issue_status"]
            )
            connection.execute(
                """
                UPDATE customer_memory
                SET resolution_keywords = ?, issue_status = ?, updated_at = ?
                WHERE memory_id = ? AND user_id = ?
                """,
                (
                    json.dumps(merged_resolution, separators=(",", ":")),
                    merged_status,
                    now,
                    row["memory_id"],
                    user.user_id,
                ),
            )
            memory_id = row["memory_id"]
        else:
            cursor = connection.execute(
                """
                INSERT INTO customer_memory
                    (user_id, issue_category, issue_keywords, resolution_keywords,
                     issue_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.user_id,
                    candidate["issue_category"],
                    keywords_json,
                    resolution_json,
                    issue_status,
                    now,
                    now,
                ),
            )
            memory_id = cursor.lastrowid
        connection.commit()
        return int(memory_id)
    except (sqlite3.Error, ValueError, TypeError):
        connection.rollback()
        raise
    finally:
        connection.close()


def retrieve_customer_memory(
    actor: DevelopmentUser | None,
    query: str,
    limit: int = 5,
    db_path: str | None = None,
) -> dict[str, Any]:
    user = _verified_actor(actor)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("A previous-issue query is required.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
        raise ValueError("The memory result limit must be between 1 and 10.")

    query_words = _canonical_words(query)
    connection = connect_db(db_path)
    try:
        rows = connection.execute(
            """
            SELECT memory_id, issue_category, issue_keywords, resolution_keywords,
                   issue_status, created_at, updated_at
            FROM customer_memory
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT 100
            """,
            (user.user_id,),
        ).fetchall()
    finally:
        connection.close()

    scored = []
    for row in rows:
        record = dict(row)
        try:
            issue_keywords = json.loads(record["issue_keywords"])
            resolution_keywords = json.loads(record["resolution_keywords"])
        except (ValueError, TypeError):
            continue
        searchable = set(issue_keywords) | set(resolution_keywords)
        searchable.add(record["issue_category"])
        searchable.update(_canonical_words(record["issue_category"]))
        overlap = query_words & searchable
        if query_words and not overlap:
            continue
        record["issue_keywords"] = issue_keywords
        record["resolution_keywords"] = resolution_keywords
        scored.append((len(overlap), record))
    scored.sort(key=lambda item: (item[0], item[1]["updated_at"]), reverse=True)
    if not scored:
        return {"records": [], "ambiguous": False}
    top_score = scored[0][0]
    top = [record for score, record in scored if score == top_score]
    if len(scored) > 1:
        matches = [record for _, record in scored[:limit]]
        return {"records": matches, "ambiguous": True}
    return {"records": [top[0]], "ambiguous": False}


def list_admin_customer_memories(
    actor: DevelopmentUser | None,
    search: str = "",
    issue_category: str = "",
    issue_status: str = "",
    limit: int = 500,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    user = _verified_actor(actor, admin=True)
    if not isinstance(search, str) or len(search) > 100:
        raise ValueError("Search text must be 100 characters or fewer.")
    if issue_category and issue_category not in ISSUE_CATEGORIES:
        raise ValueError("Unknown issue category.")
    if issue_status and issue_status not in ISSUE_STATUSES:
        raise ValueError("Unknown issue status.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("The memory result limit must be between 1 and 1000.")

    clauses = []
    params: list[Any] = []
    if search.strip():
        pattern = f"%{search.strip()}%"
        clauses.append(
            "(CAST(user_id AS TEXT) LIKE ? OR issue_category LIKE ? "
            "OR issue_keywords LIKE ? OR resolution_keywords LIKE ?)"
        )
        params.extend([pattern] * 4)
    if issue_category:
        clauses.append("issue_category = ?")
        params.append(issue_category)
    if issue_status:
        clauses.append("issue_status = ?")
        params.append(issue_status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    connection = connect_db(db_path)
    try:
        rows = connection.execute(
            f"""
            SELECT memory_id, user_id, issue_category, issue_keywords,
                   resolution_keywords, issue_status, created_at, updated_at
            FROM customer_memory
            {where}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        connection.close()

    results = []
    for row in rows:
        item = dict(row)
        item["issue_keywords"] = json.loads(item["issue_keywords"])
        item["resolution_keywords"] = json.loads(item["resolution_keywords"])
        results.append(item)
    return results


def format_admin_memory_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format compact, non-personal fields for the administrator dashboard."""
    return [
        {
            "Customer ID": record["user_id"],
            "Issue category": record["issue_category"],
            "Issue keywords": ", ".join(record["issue_keywords"]),
            "Previous resolution": ", ".join(record["resolution_keywords"])
            or "Not recorded",
            "Issue status": record["issue_status"],
            "Last updated": record["updated_at"],
        }
        for record in records
    ]


def extract_candidate(
    question: str,
    answer: str,
    tool_evidence: list[str] | None = None,
) -> dict[str, Any] | None:
    from llm_client import LLMNotConfiguredError, LLMRequestError, extract_memory_candidate

    try:
        candidate = extract_memory_candidate(
            question,
            answer,
            [item for item in (tool_evidence or []) if isinstance(item, str)],
        )
    except (LLMNotConfiguredError, LLMRequestError) as error:
        _LOGGER.warning("Customer memory extraction was skipped: %s", error)
        return None
    return _validate_candidate(candidate, question)
