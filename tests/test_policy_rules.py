from pathlib import Path

import fitz

from policy_retriever import calculate_late_return_fee, calculate_refund, select_policy_version


def test_policy_version_selection():
    assert select_policy_version("2025-02-01T00:00:00", "2025-01-01T00:00:00") == "current"
    assert select_policy_version("2024-12-01T00:00:00", "2025-01-01T00:00:00") == "legacy"


def test_refund_and_late_return_calculation():
    refund = calculate_refund(2000.0, 3, refund_percent=0.80, gst_percent=0.18)
    assert refund["refund_before_gst"] == 1600.0
    assert refund["gst"] == 288.0
    assert round(refund["total_refund"], 2) == 1888.0
    assert calculate_late_return_fee(100.0, 3, late_fee_per_hour=120.0) == 460.0


def test_pdf_extraction_works(tmp_path):
    pdf_path = tmp_path / "sample_policy.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Cancellation policy: refund is 80%. Late return fee is 120 INR per hour.")
    document.save(str(pdf_path))
    document.close()
    assert pdf_path.exists()
    doc = fitz.open(str(pdf_path))
    text = doc[0].get_text()
    doc.close()
    assert "Cancellation" in text
    assert "refund" in text.lower()
