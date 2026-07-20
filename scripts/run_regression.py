#!/usr/bin/env python3
"""Run routing, mode-quality, speaker-lock and save-integrity regressions."""

import json
import sys
from pathlib import Path

from note_io_common import normalize_text, text_sha256, verify_readback
from validate_transcript_artifact import analyze


RECAP_TERMS = ("复盘", "纪要", "总结", "核心结论", "只要核心", "行动项")
LEARNING_TERMS = ("课程", "讲座", "读书", "知识框架", "学习材料", "知识点")
BUSINESS_DIALOGUE_TERMS = ("商务", "客户", "销售", "合作", "访谈")
EXPLICIT_EXCHANGE_TERMS = ("交流实录", "保留对话", "交流过程", "像聊天记录")


def classify(text):
    if any(term in text for term in RECAP_TERMS):
        return "复盘纪要稿"
    if any(term in text for term in LEARNING_TERMS):
        return "学习整理稿"
    if any(term in text for term in EXPLICIT_EXCHANGE_TERMS):
        return "交流实录稿"
    if any(term in text for term in BUSINESS_DIALOGUE_TERMS):
        return "需要确认（推荐交流实录稿）"
    return "需要三选一"


def ratio_warning(source_chars, draft_chars):
    ratio = draft_chars / source_chars
    if ratio < 0.35:
        return "possible over-compression"
    if ratio > 0.80:
        return "possible near-verbatim搬运"
    return None


def read(root, relative):
    return (root / relative).read_text(encoding="utf-8")


def main():
    root = Path(__file__).resolve().parents[1]
    cases = json.loads(read(root, "evals/routing-cases.json"))
    quality = json.loads(read(root, "evals/mode-quality-cases.json"))
    manifest = json.loads(read(root, "manifest.json"))
    skill_text = read(root, "SKILL.md")
    failures = []

    expected_contracts = {
        "交流实录稿": "references/modes/exchange.md",
        "复盘纪要稿": "references/modes/review.md",
        "学习整理稿": "references/modes/learning.md",
    }
    if manifest["metadata"].get("branch_contracts") != expected_contracts:
        failures.append("manifest branch_contracts mismatch")
    for relative in expected_contracts.values():
        if relative not in skill_text:
            failures.append(f"SKILL.md missing mandatory branch pointer: {relative}")
        if not (root / relative).is_file():
            failures.append(f"branch contract missing: {relative}")

    for case in cases["routing_cases"]:
        actual = classify(case["input"])
        if actual != case["expected"]:
            failures.append(f"route {case['input']!r}: expected {case['expected']}, got {actual}")
    for case in cases["ratio_cases"]:
        actual = ratio_warning(case["source_chars"], case["draft_chars"])
        if actual != case["expected_warning"]:
            failures.append(f"ratio {case['draft_chars']}/{case['source_chars']}: expected {case['expected_warning']!r}, got {actual!r}")

    learning_source = read(root, quality["learning"]["source"])
    learning_good = analyze("learning", read(root, quality["learning"]["good"]), learning_source)
    learning_bad = analyze("learning", read(root, quality["learning"]["bad"]), learning_source)
    review_source = read(root, quality["review"]["source"])
    review_good = analyze("review", read(root, quality["review"]["good"]), review_source)
    review_bad = analyze("review", read(root, quality["review"]["bad"]), review_source)
    exchange_good = analyze("exchange", read(root, quality["exchange"]["good"]), read(root, quality["exchange"]["source"]), 600)
    for label, expected in (("learning good", learning_good["ok"]), ("review good", review_good["ok"]), ("exchange good", exchange_good["ok"])):
        if not expected:
            failures.append(f"{label} fixture failed")
    if learning_bad["ok"]:
        failures.append("bad learning fixture unexpectedly passed")
    if review_bad["ok"]:
        failures.append("bad review fixture unexpectedly passed")

    speaker_map = json.loads(read(root, "evals/sample-speaker-map-low-confidence.json"))
    map_invalidated = json.loads(read(root, "evals/sample-speaker-map-invalidated.json"))
    low_confidence = analyze("exchange", read(root, "evals/sample-exchange-low-confidence.md"), speaker_map=speaker_map, people_count=3, asr_channel_count=2)
    missing_map = analyze("exchange", read(root, "evals/sample-exchange-low-confidence.md"), people_count=3, asr_channel_count=2)
    guessed_name = analyze("exchange", read(root, "evals/sample-exchange-guessed-name.md"), speaker_map=speaker_map, people_count=3, asr_channel_count=2)
    invalidated = analyze("exchange", read(root, "evals/sample-exchange-low-confidence.md"), speaker_map=map_invalidated, people_count=3, asr_channel_count=2)
    wave_appendix = analyze("exchange", read(root, "evals/sample-exchange-wave-appendix.md"), speaker_map=speaker_map, people_count=3, asr_channel_count=2)
    inline_time = analyze("exchange", read(root, "evals/sample-exchange-inline-time.md"), speaker_map=speaker_map, people_count=3, asr_channel_count=2)
    expectations = {
        "low-confidence speaker map": low_confidence["ok"],
        "missing speaker map rejected": not missing_map["ok"],
        "guessed unresolved name rejected": not guessed_name["ok"],
        "invalidated speaker map rejected": not invalidated["ok"],
        "wave separator and appendix accepted": wave_appendix["ok"],
        "inline timestamp rejected": not inline_time["ok"],
    }
    failures.extend(label for label, ok in expectations.items() if not ok)

    expected = "甲\r\n乙\r\n"
    actual = {"title": "测试", "content": "甲\n乙"}
    verify_good = verify_readback(actual, "测试", expected)
    verify_bad = verify_readback({"title": "测试", "content": "甲\n丙"}, "测试", expected)
    if verify_good["read_back"] != "verified" or verify_bad["read_back"] != "mismatch":
        failures.append("normalized read-back verification regression")
    if normalize_text("甲\n\n") != "甲\n" or text_sha256("甲\r\n") != text_sha256("甲"):
        failures.append("newline normalization regression")

    script_contracts = {}
    for name in ("update-note.py", "refine-transcript.py"):
        text = read(root, f"scripts/{name}")
        ok = "os.remove" not in text and "--no-cleanup" in text and "--json" in text and "verify_readback" in text
        script_contracts[name] = ok
        if not ok:
            failures.append(f"save-script contract failed: {name}")
    refine_text = read(root, "scripts/refine-transcript.py")
    if "resolve_format" in refine_text or 'parser.error("创建模式需要由Skill明确传入 --format")' not in refine_text:
        failures.append("refine-transcript must not route manuscript type")

    report = {
        "ok": not failures,
        "routing_cases": len(cases["routing_cases"]),
        "ratio_cases": len(cases["ratio_cases"]),
        "branch_contracts": len(expected_contracts),
        "mode_quality": {"learning_good": learning_good["ok"], "learning_bad_rejected": not learning_bad["ok"], "review_good": review_good["ok"], "review_bad_rejected": not review_bad["ok"], "exchange_good": exchange_good["ok"]},
        "speaker_lock": expectations,
        "read_back": verify_good["read_back"] == "verified" and verify_bad["read_back"] == "mismatch",
        "save_scripts": script_contracts,
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
