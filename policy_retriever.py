import re
from datetime import datetime
from pathlib import Path

import fitz


class PolicyUnavailableError(RuntimeError):
    pass


def list_policy_files(policy_dir: str | Path):
    directory = Path(policy_dir)
    if not directory.exists():
        return []
    return sorted(directory.glob("*.pdf"))


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    path = Path(pdf_path)
    if not path.exists():
        raise PolicyUnavailableError(f"Policy file not found: {path}")
    document = fitz.open(str(path))
    try:
        pages = [page.get_text() for page in document]
        return "\n\n".join(pages)
    finally:
        document.close()


def select_policy_version(booking_created_at: str, policy_effective_date: str) -> str:
    booking_dt = datetime.fromisoformat(booking_created_at)
    effective_dt = datetime.fromisoformat(policy_effective_date)
    return "current" if booking_dt >= effective_dt else "legacy"


def load_policy_documents(policy_dir: str | Path):
    files = list_policy_files(policy_dir)
    if not files:
        raise PolicyUnavailableError("No policy PDFs found in the policies directory.")

    documents = []
    for pdf_path in files:
        text = extract_text_from_pdf(pdf_path)
        documents.append({"path": str(pdf_path), "name": pdf_path.name, "text": text})
    return documents


def retrieve_policy_context(question: str, policy_dir: str | Path) -> dict:
    """Return verbatim, keyword-relevant excerpts from the available PDFs."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A policy question is required.")
    documents = load_policy_documents(policy_dir)
    keywords = {
        "cancel", "cancellation", "refund", "penalty", "late", "return",
        "battery", "charging", "damage", "gst", "no-show", "deposit",
        "fee", "policy",
    }
    question_terms = {
        term.lower()
        for term in re.findall(r"[a-zA-Z][a-zA-Z-]{2,}", question)
    }
    topic_terms = question_terms & keywords
    if not topic_terms:
        topic_terms = question_terms

    excerpts = []
    for document in documents:
        sentences = re.split(r"(?<=[.!?])\s+|\n+", document["text"])
        relevant = [
            sentence.strip()[:1200]
            for sentence in sentences
            if sentence.strip()
            and any(term in sentence.lower() for term in topic_terms)
        ]
        if relevant:
            excerpts.append(
                {"document": document["name"], "excerpts": relevant[:8]}
            )

    return {
        "documents": [document["name"] for document in documents],
        "excerpts": excerpts,
        "evidence_found": bool(excerpts),
    }


def find_relevant_policy_sections(question: str, policy_documents):
    if not policy_documents:
        raise PolicyUnavailableError("No policy documents are available to answer this question.")

    normalized_question = question.lower()
    matches = []
    for document in policy_documents:
        text = document["text"]
        if any(keyword in text.lower() for keyword in ["cancellation", "refund", "late", "return", "battery", "damage", "gst", "no-show"]):
            if any(keyword in normalized_question for keyword in ["cancel", "refund", "late", "battery", "damage", "gst", "show", "policy"]):
                matches.append(document)
    if not matches:
        return policy_documents[:1]
    return matches


def calculate_refund(booking_amount: float, cancellation_days: int, refund_percent: float = 0.80, gst_percent: float = 0.18) -> dict:
    refundable = booking_amount * refund_percent
    gst = refundable * gst_percent
    total = refundable + gst
    return {
        "base_amount": booking_amount,
        "cancellation_days": cancellation_days,
        "refund_percent": refund_percent,
        "gst_percent": gst_percent,
        "refund_before_gst": refundable,
        "gst": gst,
        "total_refund": total,
    }


def calculate_late_return_fee(base_fee: float, hours_late: int, late_fee_per_hour: float = 120.0):
    return base_fee + (hours_late * late_fee_per_hour)


def calculate_cancellation_penalty(booking_amount: float, penalty_percent: float) -> float:
    """Calculate a penalty from explicit inputs; this does not determine eligibility."""
    return booking_amount * (penalty_percent / 100)


def calculate_ev_battery_fee(charging_kwh: float, rate_per_kwh: float = 18.0):
    return charging_kwh * rate_per_kwh


def policy_answer(question: str, policy_documents):
    if not policy_documents:
        raise PolicyUnavailableError("Missing policy documents. Please add the relevant PDF files to the policies folder.")

    relevant = find_relevant_policy_sections(question, policy_documents)
    snippets = []
    for document in relevant:
        text = document["text"]
        for pattern in ["cancellation", "refund", "no-show", "late return", "battery", "damage", "gst"]:
            if pattern in text.lower():
                snippets.append(f"{document['name']}: {pattern}")
                break
    return {
        "documents": [doc["name"] for doc in relevant],
        "supporting_snippets": snippets,
        "question": question,
    }
