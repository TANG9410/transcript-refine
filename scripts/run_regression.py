#!/usr/bin/env python3
"""Run routing, mode-quality, speaker-lock and save-integrity regressions."""

import json
import sys
from pathlib import Path

from note_io_common import normalize_text, text_sha256, verify_readback
from validate_transcript_artifact import analyze, retention_warning


RECAP_TERMS = ("复盘", "纪要", "总结", "核心结论", "只要核心", "行动项")
LEARNING_TERMS = ("课程", "讲座", "读书", "知识框架", "学习材料", "知识点")
BUSINESS_DIALOGUE_TERMS = ("商务", "客户", "销售", "合作", "访谈")
EXPLICIT_EXCHANGE_TERMS = ("交流实录", "保留对话", "交流过程", "像聊天记录")
NEGATED_EXCHANGE_TERMS = ("不要交流实录", "不做交流实录", "不用保留对话", "不要保留对话")
MEDIUM_INTENSITY_TERMS = ("中度", "再精简一点", "更精简")
HEAVY_INTENSITY_TERMS = ("重度", "大幅清理")


def classify(text):
    if any(term in text for term in EXPLICIT_EXCHANGE_TERMS) and not any(term in text for term in NEGATED_EXCHANGE_TERMS):
        return "交流实录稿"
    if any(term in text for term in RECAP_TERMS):
        return "复盘纪要稿"
    if any(term in text for term in LEARNING_TERMS):
        return "学习整理稿"
    if any(term in text for term in BUSINESS_DIALOGUE_TERMS):
        return "需要确认（推荐交流实录稿）"
    return "需要三选一"


def classify_exchange_intensity(text):
    normalized = text.replace("不要重度", "").replace("不用重度", "").replace("不要中度", "").replace("不用中度", "")
    if any(term in normalized for term in HEAVY_INTENSITY_TERMS):
        return "重度整理"
    if any(term in normalized for term in MEDIUM_INTENSITY_TERMS):
        return "中度精炼"
    return "轻度精炼"


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

    exchange_contract = read(root, "references/modes/exchange.md")
    required_light_contract = (
        "默认轻度精炼",
        "中度精炼",
        "重度整理（仍非摘要）",
        "禁止把不相邻发言收进同一主题",
    )
    for phrase in required_light_contract:
        if phrase not in exchange_contract:
            failures.append(f"exchange light-calibration contract missing: {phrase}")
    if "不得扩大为六种交付形式" not in skill_text:
        failures.append("SKILL.md missing current-rules-over-history precedence")

    for case in cases["routing_cases"]:
        actual = classify(case["input"])
        if actual != case["expected"]:
            failures.append(f"route {case['input']!r}: expected {case['expected']}, got {actual}")
    for case in cases["intensity_cases"]:
        actual = classify_exchange_intensity(case["input"])
        if actual != case["expected"]:
            failures.append(f"intensity {case['input']!r}: expected {case['expected']}, got {actual}")
    for case in cases["ratio_cases"]:
        actual = ratio_warning(case["source_chars"], case["draft_chars"])
        if actual != case["expected_warning"]:
            failures.append(f"ratio {case['draft_chars']}/{case['source_chars']}: expected {case['expected_warning']!r}, got {actual!r}")
    _, exchange_high_warning = retention_warning("稿" * 9000, "源" * 10000, warn_near_verbatim=False)
    _, generic_high_warning = retention_warning("稿" * 9000, "源" * 10000)
    if exchange_high_warning is not None or generic_high_warning is None:
        failures.append("exchange high-retention warning policy regression")

    learning_source = read(root, quality["learning"]["source"])
    learning_good = analyze("learning", read(root, quality["learning"]["good"]), learning_source)
    learning_bad = analyze("learning", read(root, quality["learning"]["bad"]), learning_source)
    review_source = read(root, quality["review"]["source"])
    review_good = analyze("review", read(root, quality["review"]["good"]), review_source)
    review_bad = analyze("review", read(root, quality["review"]["bad"]), review_source)
    exchange_good = analyze("exchange", read(root, quality["exchange"]["good"]), read(root, quality["exchange"]["source"]), 600)
    untimed_source = "甲：这个方案我觉得还要再看看，明天 10:30 再开会。\n乙：好，我们继续讨论。"
    untimed_good = analyze("exchange", "# 交流实录\n\n## 方案讨论\n\n**甲：** 这个方案我觉得还要再看看，明天 10:30 再开会。\n\n**乙：** 好，我们继续讨论。", untimed_source)
    untimed_invented = analyze("exchange", "# 交流实录\n\n## 方案讨论 [00:00-00:30]\n\n**甲：** 这个方案我觉得还要再看看，明天 10:30 再开会。", untimed_source)
    for label, expected in (("learning good", learning_good["ok"]), ("review good", review_good["ok"]), ("exchange good", exchange_good["ok"])):
        if not expected:
            failures.append(f"{label} fixture failed")
    if learning_bad["ok"]:
        failures.append("bad learning fixture unexpectedly passed")
    if review_bad["ok"]:
        failures.append("bad review fixture unexpectedly passed")
    if not untimed_good["ok"] or untimed_invented["ok"]:
        failures.append("untimed exchange heading policy regression")

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
        "validation_scope": "deterministic structure, routing, speaker and save contracts only",
        "routing_cases": len(cases["routing_cases"]),
        "intensity_cases": len(cases["intensity_cases"]),
        "ratio_cases": len(cases["ratio_cases"]),
        "branch_contracts": len(expected_contracts),
        "mode_quality": {"learning_good": learning_good["ok"], "learning_bad_rejected": not learning_bad["ok"], "review_good": review_good["ok"], "review_bad_rejected": not review_bad["ok"], "exchange_good": exchange_good["ok"], "untimed_exchange_good": untimed_good["ok"], "untimed_exchange_invented_time_rejected": not untimed_invented["ok"]},
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
