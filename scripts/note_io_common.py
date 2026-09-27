#!/usr/bin/env python3
"""Get笔记保存脚本共用的确定性 read-back 校验。"""

import hashlib
import json
import re


VALIDATION_REPORT_CONTRACT = "transcript-refine-v3-final-ready-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_MODES = {"exchange", "review", "learning"}
REQUIRED_REVIEW_SAMPLES = {"opening", "middle", "ending"}
REQUIRED_RISK_CHECKS = {
    "numbers_prices",
    "questions_objections",
    "commitments_next_steps",
    "speaker_asr_uncertainty",
    "technical_interlude",
}


def normalize_text(text):
    """统一换行，只忽略正文末尾一个换行。"""
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if value.startswith("\ufeff"):
        value = value[1:]
    return value[:-1] if value.endswith("\n") else value


def text_sha256(text):
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def json_sha256(value):
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def validate_review_evidence_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("result") != "pass":
        raise ValueError("validation report review evidence is incomplete")
    samples = snapshot.get("samples")
    if not isinstance(samples, list):
        raise ValueError("validation report review samples are missing")
    sample_kinds = set()
    for item in samples:
        if not isinstance(item, dict):
            raise ValueError("validation report review sample is invalid")
        sample_kinds.add(item.get("kind"))
        if item.get("status") != "pass" or item.get("unresolved_items", []) or item.get("blocking_issues", []):
            raise ValueError("validation report review sample is unresolved")
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("source_quote", "draft_quote")):
            raise ValueError("validation report review sample lacks source-draft evidence")
        if not isinstance(item.get("preserved_items"), list) or not any(isinstance(x, str) and x.strip() for x in item["preserved_items"]):
            raise ValueError("validation report review sample lacks preserved items")
    if not REQUIRED_REVIEW_SAMPLES.issubset(sample_kinds):
        raise ValueError("validation report is missing opening/middle/ending evidence")

    risks = snapshot.get("risk_checks")
    if not isinstance(risks, list):
        raise ValueError("validation report risk evidence is missing")
    risk_kinds = set()
    for item in risks:
        if not isinstance(item, dict):
            raise ValueError("validation report risk evidence is invalid")
        risk_kinds.add(item.get("kind"))
        if item.get("status") not in {"checked", "not_present"}:
            raise ValueError("validation report risk status is invalid")
        if item.get("unresolved_items", []) or item.get("blocking_issues", []):
            raise ValueError("validation report risk evidence is unresolved")
        if item.get("status") == "checked":
            for key in ("source_quotes", "draft_quotes", "preserved_items"):
                values = item.get(key)
                if not isinstance(values, list) or not any(isinstance(x, str) and x.strip() for x in values):
                    raise ValueError(f"validation report checked risk lacks {key}")
    if not REQUIRED_RISK_CHECKS.issubset(risk_kinds):
        raise ValueError("validation report is missing required risk evidence")


def require_final_validation(report_path, expected_content, expected_mode=None):
    if not report_path:
        raise ValueError("保存正文需要 --validation-report")
    with open(report_path, "r", encoding="utf-8-sig") as handle:
        report = json.load(handle)
    if not isinstance(report, dict):
        raise ValueError("validation report must be a JSON object")
    if report.get("validation_report_contract") != VALIDATION_REPORT_CONTRACT:
        raise ValueError("validation report contract mismatch")
    if report.get("final_ready") is not True:
        raise ValueError("validation report final_ready must be true")
    if report.get("ok") is not True or report.get("structural_ok") is not True:
        raise ValueError("validation report structural status must be true")
    if report.get("final_ready_reason") not in {
        "source_aligned_review_complete",  # Legacy v1 reports remain compatible.
        "review_report_traceability_complete",
    }:
        raise ValueError("validation report final_ready_reason mismatch")
    if report.get("validation_scope") != "structure_and_traceability":
        raise ValueError("validation report scope mismatch")
    if report.get("semantic_review_required") is not False:
        raise ValueError("validation report still requires semantic review")
    if report.get("source_aligned_review_status") != "complete":
        raise ValueError("validation report source-aligned review is incomplete")
    if report.get("issues") != [] or report.get("final_issues") != []:
        raise ValueError("validation report contains unresolved issues")
    if report.get("mode") not in ALLOWED_MODES:
        raise ValueError("validation report mode is invalid")
    source_hash = report.get("source_sha256")
    draft_hash = report.get("draft_sha256")
    if not isinstance(source_hash, str) or not SHA256_RE.fullmatch(source_hash):
        raise ValueError("validation report source_sha256 is invalid")
    if not isinstance(draft_hash, str) or not SHA256_RE.fullmatch(draft_hash):
        raise ValueError("validation report draft_sha256 is invalid")
    stats = report.get("stats")
    if not isinstance(stats, dict) or not isinstance(stats.get("source_chars"), int) or stats["source_chars"] <= 0:
        raise ValueError("validation report has no non-empty fact source")
    if stats.get("review_result") != "pass":
        raise ValueError("validation report review_result must be pass")
    if not REQUIRED_REVIEW_SAMPLES.issubset(set(stats.get("review_sample_kinds") or [])):
        raise ValueError("validation report is missing opening/middle/ending review")
    if not REQUIRED_RISK_CHECKS.issubset(set(stats.get("review_risk_kinds") or [])):
        raise ValueError("validation report is missing required risk checks")
    review_hash = report.get("review_report_sha256")
    if not isinstance(review_hash, str) or not SHA256_RE.fullmatch(review_hash):
        raise ValueError("validation report review_report_sha256 is invalid")
    evidence = report.get("review_evidence")
    validate_review_evidence_snapshot(evidence)
    if report.get("review_evidence_sha256") != json_sha256(evidence):
        raise ValueError("validation report review evidence digest mismatch")
    expected_hash = text_sha256(expected_content)
    if draft_hash != expected_hash:
        raise ValueError("validation report draft_sha256 does not match content")
    if expected_mode is not None and report.get("mode") != expected_mode:
        raise ValueError("validation report mode does not match requested format")
    return report


def verify_readback(note, expected_title=None, expected_content=None):
    actual_title = note.get("title")
    actual_content = note.get("content", "")
    title_verified = expected_title is None or actual_title == expected_title
    content_verified = (
        expected_content is None
        or normalize_text(actual_content) == normalize_text(expected_content)
    )
    return {
        "title_verified": title_verified,
        "content_verified": content_verified,
        "content_chars": len(normalize_text(actual_content)),
        "content_sha256": text_sha256(actual_content),
        "read_back": "verified" if title_verified and content_verified else "mismatch",
    }
