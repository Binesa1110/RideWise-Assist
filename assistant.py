from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from typing import Any

from access_control import DEFAULT_ACCESS_CONTROL, DevelopmentUser
from analytics import AdminAuthorizationError, get_admin_analytics
from config import get_policy_dir, is_llm_configured
from customer_memory import (
    extract_candidate,
    is_previous_issue_query,
    retrieve_customer_memory,
    save_customer_memory,
)
from database import get_agent_booking_by_ref, get_agent_damage_reports
from llm_client import (
    LLMNotConfiguredError,
    LLMRequestError,
    complete_agent_turn,
)
from policy_retriever import (
    PolicyUnavailableError,
    calculate_cancellation_penalty,
    calculate_late_return_fee,
    calculate_refund,
    retrieve_policy_context,
)


MAX_TOOL_CALLS = 4
MAX_TOOL_RESULT_CHARS = 10000
BOOKING_REF_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,30}$")
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])\d+(?:,\d{3})*(?:\.\d+)?")
_LOGGER = logging.getLogger(__name__)


def infer_intent(question: str) -> str:
    lowered = question.lower()
    if any(keyword in lowered for keyword in ["payment", "charged", "charge", "invoice", "paid"]):
        return "payment"
    if any(keyword in lowered for keyword in ["cancel", "cancellation", "refund", "chargeback"]):
        return "cancellation"
    if any(keyword in lowered for keyword in ["late", "return", "overdue"]):
        return "late_return"
    if any(keyword in lowered for keyword in ["battery", "ev", "charging"]):
        return "battery"
    if any(keyword in lowered for keyword in ["damage", "scratch", "repair"]):
        return "damage"
    if any(keyword in lowered for keyword in ["booking", "reservation", "trip", "vehicle"]):
        return "booking"
    if any(keyword in lowered for keyword in ["policy", "gst", "rule"]):
        return "policy"
    if any(keyword in lowered for keyword in ["analytics", "dashboard", "total bookings"]):
        return "analytics"
    return "general"


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


AGENT_TOOLS = [
    _tool(
        "lookup_booking",
        "Look up an existing booking by its exact booking reference. Do not request or pass user/customer IDs.",
        {"booking_ref": {"type": "string"}},
        ["booking_ref"],
    ),
    _tool(
        "retrieve_policy",
        "Retrieve verbatim relevant excerpts from the available rental policy PDFs. Use this before answering policy or eligibility questions.",
        {"question": {"type": "string"}},
        ["question"],
    ),
    _tool(
        "lookup_damage_reports",
        "Look up damage reports associated with a booking reference, subject to the current user's record access.",
        {"booking_ref": {"type": "string"}},
        ["booking_ref"],
    ),
    _tool(
        "calculate_refund",
        "Perform arithmetic using only explicitly stated or verified booking amount, cancellation days, refund fraction (for example 0.8 for 80%), and GST fraction. This does not establish eligibility. Do not assume omitted percentages.",
        {
            "booking_amount": {"type": "number"},
            "cancellation_days": {"type": "integer"},
            "refund_percent": {"type": "number"},
            "gst_percent": {"type": "number"},
        },
        ["booking_amount", "cancellation_days", "refund_percent", "gst_percent"],
    ),
    _tool(
        "calculate_cancellation_penalty",
        "Calculate the arithmetic penalty using an explicitly stated or verified amount and explicitly stated penalty percentage (0 to 100). This does not establish eligibility or policy.",
        {
            "booking_amount": {"type": "number"},
            "penalty_percent": {"type": "number"},
        },
        ["booking_amount", "penalty_percent"],
    ),
    _tool(
        "calculate_late_return_fee",
        "Calculate a late fee using explicitly stated base fee, whole hours late, and per-hour rate. Do not assume a rate.",
        {
            "base_fee": {"type": "number"},
            "hours_late": {"type": "integer"},
            "late_fee_per_hour": {"type": "number"},
        },
        ["base_fee", "hours_late", "late_fee_per_hour"],
    ),
    _tool(
        "admin_analytics",
        "Retrieve aggregate admin analytics. This tool is restricted to the verified administrator.",
        {},
        [],
    ),
]

_TOOL_INTENTS = {
    "lookup_booking": "booking",
    "retrieve_policy": "policy",
    "lookup_damage_reports": "damage",
    "calculate_refund": "cancellation",
    "calculate_cancellation_penalty": "cancellation",
    "calculate_late_return_fee": "late_return",
    "admin_analytics": "analytics",
}


class AgentToolError(ValueError):
    """Raised for invalid or unauthorized agent tool requests."""


def _require_arguments(arguments: dict[str, Any], expected: set[str]) -> None:
    if set(arguments) != expected:
        raise AgentToolError("The tool arguments were incomplete or unsupported.")


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgentToolError(f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise AgentToolError(f"{name} is outside the allowed range.")
    return number


def _explicit_user_numbers(question: str) -> list[float]:
    without_references = re.sub(
        r"\b[A-Z]{1,10}-\d+\b", "", question, flags=re.IGNORECASE
    )
    return [
        float(value.replace(",", ""))
        for value in _NUMBER_PATTERN.findall(without_references)
    ]


def _require_explicit_value(
    value: float, name: str, question: str, verified_values: set[float]
) -> None:
    if value in verified_values:
        return
    user_values = _explicit_user_numbers(question)
    candidates = {value}
    if name.endswith("_percent") or name == "refund_percent" or name == "gst_percent":
        candidates.add(value * 100)
    if not any(math.isclose(candidate, supplied, rel_tol=1e-9, abs_tol=1e-9) for candidate in candidates for supplied in user_values):
        raise AgentToolError(
            f"{name} must be stated by the user or verified from an authorized record."
        )


def _booking_reference(arguments: dict[str, Any]) -> str:
    value = arguments.get("booking_ref")
    if not isinstance(value, str) or not BOOKING_REF_PATTERN.fullmatch(value.strip()):
        raise AgentToolError("Provide a valid booking reference.")
    return value.strip()


def _requires_policy_evidence(question: str) -> bool:
    return bool(
        re.search(
            r"\b(policy|eligible|eligibility|entitled|allowed|will i get|"
            r"can i get|do i get|am i due|"
            r"refund policy|cancellation policy|cancel(?:ling)? my booking)\b",
            question,
            re.IGNORECASE,
        )
    )


def _meaningful_customer_issue(question: str) -> bool:
    return bool(
        re.search(
            r"\b(problem|issue|not working|doesn't work|cannot|can't|failed|"
            r"failure|error|wrong|missing|lost|damaged|scratch|late|overdue|"
            r"refund|cancel|charged|charge|payment|complaint|concern|"
            r"booking|reservation)\b",
            question,
            re.IGNORECASE,
        )
    )


def _validated_tool_result(result: Any) -> str:
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return json.dumps({"error": "Tool data could not be validated."})
    if len(encoded) > MAX_TOOL_RESULT_CHARS:
        return json.dumps({"error": "The result is too large to safely include."})
    return encoded


def _validate_tool_output(name: str, result: Any) -> Any:
    if isinstance(result, dict) and "error" in result:
        if not isinstance(result["error"], str):
            raise AgentToolError("The tool returned an invalid error result.")
        return result
    if name == "lookup_booking":
        if not isinstance(result, dict) or not isinstance(result.get("found"), bool):
            raise AgentToolError("The booking result was invalid.")
        if result["found"] is False:
            return result
        booking = result.get("booking")
        if not isinstance(booking, dict):
            raise AgentToolError("The booking result was invalid.")
        allowed = {
            "booking_ref", "start_time", "end_time", "status", "total_amount",
            "vehicle_make", "vehicle_model", "vehicle_type",
        }
        if (
            set(booking) - allowed
            or not isinstance(booking.get("booking_ref"), str)
            or not isinstance(booking.get("status"), str)
            or isinstance(booking.get("total_amount"), bool)
            or not isinstance(booking.get("total_amount"), (int, float))
            or not math.isfinite(float(booking["total_amount"]))
        ):
            raise AgentToolError("The booking result was invalid.")
    elif name == "lookup_damage_reports":
        reports = result.get("reports") if isinstance(result, dict) else None
        if not isinstance(reports, list) or len(reports) > 100:
            raise AgentToolError("The damage report result was invalid.")
        allowed = {"status", "description", "estimated_cost", "created_at"}
        for report in reports:
            if (
                not isinstance(report, dict)
                or set(report) != allowed
                or not all(isinstance(report[field], str) for field in ("status", "description", "created_at"))
                or isinstance(report["estimated_cost"], bool)
                or not isinstance(report["estimated_cost"], (int, float))
                or not math.isfinite(float(report["estimated_cost"]))
            ):
                raise AgentToolError("The damage report result was invalid.")
    elif name == "retrieve_policy":
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("evidence_found"), bool)
            or not isinstance(result.get("documents"), list)
            or not all(isinstance(document, str) for document in result["documents"])
            or not isinstance(result.get("excerpts"), list)
        ):
            raise AgentToolError("The policy result was invalid.")
        for excerpt in result["excerpts"]:
            if (
                not isinstance(excerpt, dict)
                or not isinstance(excerpt.get("document"), str)
                or not isinstance(excerpt.get("excerpts"), list)
                or not all(isinstance(text, str) for text in excerpt["excerpts"])
            ):
                raise AgentToolError("The policy result was invalid.")
    elif name == "admin_analytics":
        if not isinstance(result, dict) or not all(
            key in result for key in ("bookings", "payments", "damage_reports", "coverage")
        ):
            raise AgentToolError("The analytics result was invalid.")
    elif name in {
        "calculate_refund",
        "calculate_cancellation_penalty",
        "calculate_late_return_fee",
    }:
        if not isinstance(result, dict) or result.get("calculation_only") is not True:
            raise AgentToolError("The calculation result was invalid.")
    return result


def _execute_tool(
    name: str,
    arguments: Any,
    actor: DevelopmentUser,
    question: str,
    verified_values: set[float],
) -> str:
    if not isinstance(arguments, dict):
        return _validated_tool_result({"error": "Tool arguments must be an object."})

    try:
        if name == "lookup_booking":
            _require_arguments(arguments, {"booking_ref"})
            result = get_agent_booking_by_ref(actor, _booking_reference(arguments))
            if result is None:
                result = {"found": False, "message": "No accessible booking was found with that reference."}
            else:
                result = {"found": True, "booking": result}
                amount = result["booking"].get("total_amount")
                if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                    verified_values.add(float(amount))
        elif name == "lookup_damage_reports":
            _require_arguments(arguments, {"booking_ref"})
            result = {
                "reports": get_agent_damage_reports(
                    actor, _booking_reference(arguments)
                )
            }
        elif name == "retrieve_policy":
            _require_arguments(arguments, {"question"})
            policy_question = arguments.get("question")
            if not isinstance(policy_question, str) or not policy_question.strip() or len(policy_question) > 500:
                raise AgentToolError("Provide a policy question under 500 characters.")
            result = retrieve_policy_context(policy_question, get_policy_dir())
        elif name == "calculate_refund":
            _require_arguments(
                arguments,
                {"booking_amount", "cancellation_days", "refund_percent", "gst_percent"},
            )
            amount = _number(arguments["booking_amount"], "booking_amount", 0, 10_000_000)
            days = arguments["cancellation_days"]
            if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 3650:
                raise AgentToolError("cancellation_days must be a whole number from 0 to 3650.")
            refund_percent = _number(arguments["refund_percent"], "refund_percent", 0, 1)
            gst_percent = _number(arguments["gst_percent"], "gst_percent", 0, 1)
            for value, field in ((amount, "booking_amount"), (float(days), "cancellation_days"), (refund_percent, "refund_percent"), (gst_percent, "gst_percent")):
                _require_explicit_value(value, field, question, verified_values)
            result = {
                "calculation_only": True,
                "eligibility_determined": False,
                **calculate_refund(amount, days, refund_percent, gst_percent),
            }
        elif name == "calculate_cancellation_penalty":
            _require_arguments(arguments, {"booking_amount", "penalty_percent"})
            amount = _number(arguments["booking_amount"], "booking_amount", 0, 10_000_000)
            percent = _number(arguments["penalty_percent"], "penalty_percent", 0, 100)
            _require_explicit_value(amount, "booking_amount", question, verified_values)
            _require_explicit_value(percent, "penalty_percent", question, verified_values)
            result = {
                "calculation_only": True,
                "eligibility_determined": False,
                "booking_amount": amount,
                "penalty_percent": percent,
                "cancellation_penalty": calculate_cancellation_penalty(amount, percent),
            }
        elif name == "calculate_late_return_fee":
            _require_arguments(arguments, {"base_fee", "hours_late", "late_fee_per_hour"})
            base_fee = _number(arguments["base_fee"], "base_fee", 0, 10_000_000)
            hours = arguments["hours_late"]
            if isinstance(hours, bool) or not isinstance(hours, int) or not 0 <= hours <= 87600:
                raise AgentToolError("hours_late must be a whole number from 0 to 87600.")
            rate = _number(arguments["late_fee_per_hour"], "late_fee_per_hour", 0, 1_000_000)
            for value, field in ((base_fee, "base_fee"), (float(hours), "hours_late"), (rate, "late_fee_per_hour")):
                _require_explicit_value(value, field, question, verified_values)
            result = {
                "calculation_only": True,
                "late_return_fee": calculate_late_return_fee(base_fee, hours, rate),
            }
        elif name == "admin_analytics":
            _require_arguments(arguments, set())
            result = get_admin_analytics(actor)
        else:
            result = {"error": "This tool is not available."}
    except AgentToolError as exc:
        result = {"error": str(exc)}
    except (PermissionError, AdminAuthorizationError):
        result = {"error": "You are not authorized to access that information."}
    except PolicyUnavailableError as exc:
        result = {"error": str(exc)}
    except sqlite3.Error:
        result = {"error": "The database could not complete this lookup."}
    except (ValueError, TypeError):
        result = {"error": "The tool could not use the supplied arguments."}
    try:
        result = _validate_tool_output(name, result)
    except AgentToolError as exc:
        result = {"error": str(exc)}
    return _validated_tool_result(result)


class RideWiseAssistant:
    def __init__(self, user: DevelopmentUser | None = None):
        self.user = user

    def answer_question(self, question: str, booking_ref: str | None = None):
        if not isinstance(question, str) or not question.strip():
            return {"answer": "Please provide a complete question so I can help you.", "intent": "invalid"}
        if booking_ref:
            if not isinstance(booking_ref, str) or not booking_ref.strip():
                return {"answer": "Please provide a valid booking reference.", "intent": "invalid"}
            question = f"{question.strip()}\nBooking reference: {booking_ref.strip()}"
        if not is_llm_configured():
            return {
                "answer": "The NeoStats endpoint is not configured. Live agent responses are unavailable.",
                "intent": infer_intent(question),
                "status": "llm_missing",
            }

        user = DEFAULT_ACCESS_CONTROL.resolve_verified_user(self.user)
        if user is None:
            return {
                "answer": "A verified application user is required to use the agent tools.",
                "intent": infer_intent(question),
                "status": "unauthorized",
            }

        original_question = question.strip()
        memory_context = ""
        if user.role == "customer" and is_previous_issue_query(original_question):
            try:
                previous = retrieve_customer_memory(user, original_question)
            except (PermissionError, ValueError, sqlite3.Error):
                previous = {"records": [], "ambiguous": False}
            records = previous["records"]
            if not records:
                return {
                    "answer": (
                        "I couldn't find a matching previous issue in your saved customer "
                        "memory. Could you briefly describe the problem?"
                    ),
                    "intent": infer_intent(original_question),
                    "status": "previous_issue_not_found",
                }
            if previous["ambiguous"]:
                options = "; ".join(
                    f"{record['issue_category']}: {', '.join(record['issue_keywords'])}"
                    for record in records[:3]
                )
                return {
                    "answer": (
                        f"I found multiple previous issues ({options}). Which one "
                        "are you asking about?"
                    ),
                    "intent": infer_intent(original_question),
                    "status": "previous_issue_ambiguous",
                }
            record = records[0]
            memory_context = (
                "\n\nVerified saved customer-memory record (use only as historical "
                "context, not as proof of current status):\n"
                f"category={record['issue_category']}; "
                f"issue_keywords={', '.join(record['issue_keywords'])}; "
                f"resolution_keywords={', '.join(record['resolution_keywords']) or 'none recorded'}; "
                f"issue_status={record['issue_status']}; "
                f"last_updated={record['updated_at']}."
            )

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": original_question + memory_context,
            }
        ]
        verified_values: set[float] = set()
        tools_used: list[str] = []
        policy_evidence_available = False
        try:
            for _ in range(MAX_TOOL_CALLS + 1):
                response = complete_agent_turn(messages, AGENT_TOOLS)
                calls = response.tool_calls or []
                if not calls:
                    content = response.content
                    if not isinstance(content, str) or not content.strip():
                        return {
                            "answer": "NeoStats returned no response. Please try again.",
                            "intent": _TOOL_INTENTS.get(tools_used[0], infer_intent(question)) if tools_used else infer_intent(question),
                            "status": "llm_error",
                        }
                    if (
                        _requires_policy_evidence(original_question)
                        and not policy_evidence_available
                    ):
                        return {
                            "answer": (
                                "I can’t determine policy eligibility because no relevant "
                                "verified policy excerpt was retrieved. Please add or identify "
                                "the applicable rental policy document."
                            ),
                            "intent": "policy",
                            "status": "policy_evidence_missing",
                            "tools_used": tools_used,
                        }
                    if user.role == "customer" and _meaningful_customer_issue(original_question):
                        tool_evidence = [
                            message["content"]
                            for message in messages
                            if message.get("role") == "tool"
                            and isinstance(message.get("content"), str)
                        ]
                        candidate = extract_candidate(
                            original_question, content.strip(), tool_evidence
                        )
                        if candidate:
                            try:
                                save_customer_memory(
                                    user,
                                    candidate,
                                    original_question,
                                    tool_evidence,
                                )
                            except (PermissionError, ValueError, sqlite3.Error) as error:
                                _LOGGER.warning(
                                    "Customer memory could not be saved: %s", error
                                )
                    return {
                        "answer": content.strip(),
                        "intent": _TOOL_INTENTS.get(tools_used[0], infer_intent(question)) if tools_used else infer_intent(question),
                        "tools_used": tools_used,
                    }

                serialized_calls = []
                for call in calls:
                    function = call.function
                    serialized_calls.append(
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": function.name,
                                "arguments": function.arguments,
                            },
                        }
                    )
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": serialized_calls,
                    }
                )

                for call in calls:
                    try:
                        arguments = json.loads(call.function.arguments)
                    except (TypeError, json.JSONDecodeError):
                        arguments = None
                    if len(tools_used) >= MAX_TOOL_CALLS:
                        result = _validated_tool_result(
                            {"error": "The safe tool-call limit was reached."}
                        )
                    else:
                        tools_used.append(call.function.name)
                        result = _execute_tool(
                            call.function.name,
                            arguments,
                            user,
                            question,
                            verified_values,
                        )
                        if call.function.name == "retrieve_policy":
                            try:
                                policy_evidence_available = bool(
                                    json.loads(result).get("evidence_found")
                                )
                            except (TypeError, ValueError):
                                policy_evidence_available = False
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": result,
                        }
                    )

            return {
                "answer": "I could not complete this request within the safe tool-call limit. Please narrow the request and try again.",
                "intent": _TOOL_INTENTS.get(tools_used[0], infer_intent(question)) if tools_used else infer_intent(question),
                "status": "tool_limit",
                "tools_used": tools_used,
            }
        except LLMNotConfiguredError as error:
            return {"answer": str(error), "intent": infer_intent(question), "status": "llm_missing"}
        except LLMRequestError as error:
            return {"answer": str(error), "intent": infer_intent(question), "status": "llm_error"}
