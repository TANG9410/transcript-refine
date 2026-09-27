#!/usr/bin/env python3
"""Run routing, mode-quality, speaker-lock and save-integrity regressions."""

import contextlib
import io
import json
import sys
from pathlib import Path

from note_io_common import normalize_text, text_sha256, verify_readback
from validate_transcript_artifact import analyze, main as validate_main, retention_warning


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


def needs_split_proposal(source_chars):
    return source_chars > 35000


def needs_multi_source_branch(fact_source_count, explicit_merge=False):
    return explicit_merge or fact_source_count >= 2


def read(root, relative):
    return (root / relative).read_text(encoding="utf-8")


def make_review_report(mode, source_text, draft_text, speaker_map_text=None, result="pass"):
    source_quote = next((line.strip() for line in source_text.splitlines() if len(line.strip()) >= 4), "source")
    draft_quote = next((line.strip() for line in draft_text.splitlines() if len(line.strip()) >= 4), "draft")
    samples = [
        {
            "kind": kind,
            "source_locator": f"{kind}-source",
            "draft_locator": f"{kind}-draft",
            "source_quote": source_quote,
            "draft_quote": draft_quote,
            "preserved_items": [f"{kind}-evidence"],
            "unresolved_items": [],
            "blocking_issues": [] if result == "pass" else ["fixture intentionally rejected"],
            "status": "pass" if result == "pass" else "fail",
        }
        for kind in ("opening", "middle", "ending")
    ]
    report = {
        "version": 1,
        "mode": mode,
        "source_sha256": text_sha256(source_text),
        "draft_sha256": text_sha256(draft_text),
        "samples": samples,
        "risk_checks": [
            {
                "kind": kind,
                "status": "checked",
                "source_locators": ["fixture-source"],
                "draft_locators": ["fixture-draft"],
                "source_quotes": [source_quote],
                "draft_quotes": [draft_quote],
                "preserved_items": ["fixture evidence"],
                "blocking_issues": [] if result == "pass" else ["fixture intentionally rejected"],
            }
            for kind in (
                "numbers_prices",
                "questions_objections",
                "commitments_next_steps",
                "speaker_asr_uncertainty",
                "technical_interlude",
            )
        ],
        "result": result,
    }
    if speaker_map_text is not None:
        report["speaker_map_sha256"] = text_sha256(speaker_map_text)
    return report


def make_fake_review_report(mode, source_text, draft_text):
    report = make_review_report(mode, source_text, draft_text)
    for item in report["samples"]:
        item.pop("source_quote", None)
        item.pop("draft_quote", None)
        item["preserved_items"] = ["已核对"]
    return report


def risk_evidence_regressions():
    """Exercise real validator decisions; every submitted report claims pass."""
    import copy
    from validate_transcript_artifact import REQUIRED_RISK_CHECKS, risk_signal_present

    outcomes = {}

    def fixture(source, kind, status="checked", reason=None):
        draft = "# 测试交流实录\n\n## 讨论\n\n**甲：** " + source
        report = make_review_report("exchange", source, draft)
        report["risk_checks"] = [
            {"kind": k, "status": "not_present", "blocking_issues": []}
            for k in sorted(REQUIRED_RISK_CHECKS)
        ]
        item = next(x for x in report["risk_checks"] if x["kind"] == kind)
        item.update(status=status, source_locators=["原文第1行"],
                    draft_locators=["甲的发言"], source_quotes=[source],
                    draft_quotes=[source], preserved_items=["核对该句的语境及原有含义"])
        if reason is not None:
            item["semantic_reason"] = reason
        return source, draft, report, item

    def ready(f):
        return analyze("exchange", f[1], f[0], final_check=True,
                       review_report=f[2])["final_ready"]

    for text in ("报价三百块钱。", "预算十来万。", "预算四五千。", "分成百分之六十。", "分成十五个点。"):
        outcomes["chinese number detected: " + text] = risk_signal_present("numbers_prices", text)
        f = fixture(text, "numbers_prices")
        outcomes["chinese number checked: " + text] = ready(f)
        f[3]["status"] = "not_present"
        outcomes["chinese number hidden without reason rejected: " + text] = not ready(f)

    f = fixture("这些资料我来准备。", "commitments_next_steps")
    outcomes["lexical miss without reason rejected"] = not ready(f)
    f[3]["semantic_reason"] = "发言人明确承担准备资料的后续工作，原句没有词表中的固定动词。"
    outcomes["lexical miss with contextual evidence accepted"] = ready(f)
    no_evidence = copy.deepcopy(f)
    no_evidence[3]["draft_quotes"] = []
    outcomes["reason cannot replace draft evidence"] = not ready(no_evidence)
    fake_source = copy.deepcopy(f)
    fake_source[3]["source_quotes"].append("原文没有的发言也被当作证据。")
    outcomes["one real quote cannot cover a fake source quote"] = not ready(fake_source)
    fake_draft = copy.deepcopy(f)
    fake_draft[3]["draft_quotes"].append("稿件没有的发言也被当作证据。")
    outcomes["one real quote cannot cover a fake draft quote"] = not ready(fake_draft)
    placeholder = copy.deepcopy(f)
    placeholder[3]["semantic_reason"] = "已核对。"
    outcomes["generic reason rejected"] = not ready(placeholder)

    f = fixture("今天练的曲目叫《一万元》。", "numbers_prices", "not_present")
    outcomes["false positive needs explanation"] = not ready(f)
    f[3]["semantic_reason"] = "一万元是这句中提及的曲目名称，这里没有交易、报价或金额约定。"
    outcomes["false positive with source evidence accepted"] = ready(f)
    for name, field, value in (
        ("missing source", "source_quotes", []),
        ("fake source", "source_quotes", ["原文中不存在的其他曲目名称。"]),
        ("missing locator", "source_locators", []),
    ):
        bad = copy.deepcopy(f)
        bad[3][field] = value
        outcomes["false positive override rejects " + name] = not ready(bad)

    # Confirmation-only replies are valid content; deletion is not a validator requirement.
    source = "甲：报价两万元，对吧？\n乙：对。"
    draft = "# 确认\n\n## 报价\n\n**甲：** 报价两万元，对吧？\n\n**乙：** 对。"
    outcomes["meaningful short confirmation structurally accepted"] = analyze("exchange", draft, source)["ok"]
    absent = fixture("双方互相问候。", "numbers_prices", "not_present")
    absent[3]["source_quotes"] = []
    absent[3]["draft_quotes"] = []
    outcomes["ordinary not_present keeps empty optional quote arrays compatible"] = ready(absent)
    return outcomes


def main():
    root = Path(__file__).resolve().parents[1]
    cases = json.loads(read(root, "evals/routing-cases.json"))
    quality = json.loads(read(root, "evals/mode-quality-cases.json"))
    manifest = json.loads(read(root, "manifest.json"))
    skill_text = read(root, "SKILL.md")
    failures = []
    missing_source_rejected = False
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            validate_main(
                [
                    "--mode",
                    "exchange",
                    "--draft",
                    str(root / "evals/sample-exchange.md"),
                    "--speaker-state",
                    "stable",
                    "--json",
                ]
            )
        except SystemExit as exc:
            missing_source_rejected = exc.code == 2
    if not missing_source_rejected:
        failures.append("validator CLI did not require --source")

    bom_final_check_accepted = False
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            bom_final_check_accepted = validate_main(
                [
                    "--mode",
                    "exchange",
                    "--draft",
                    str(root / "evals/sample-utf8-sig-draft.md"),
                    "--source",
                    str(root / "evals/sample-utf8-sig-source.txt"),
                    "--speaker-state",
                    "unstable",
                    "--speaker-map",
                    str(root / "evals/sample-utf8-sig-speaker-map.json"),
                    "--final-check",
                    "--review-report",
                    str(root / "evals/sample-utf8-sig-review.json"),
                ]
            ) == 0
        except SystemExit as exc:
            bom_final_check_accepted = exc.code == 0
    if not bom_final_check_accepted:
        failures.append("validator did not accept utf-8-sig source/draft/speaker/review inputs")

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

    # Check the current short instructions, not retired bottom-level CLI prose.
    # These are documentation guards; behavior tests below remain independent.
    required_core_contract = (
        "默认读本文件和所选模式",
        "同一 ASR 错词、标题和摘要不能相互自证",
        "证据不足保留原词并在对应位置注明疑点",
        "归属不稳定时建立并持续修正",
        "`10000` 字和录音时长不触发固定切块",
        "超过 `35000` 字符",
        "单源完全跳过",
        "保留率仅统计，不作质量门槛",
        "排障或兼容旧命令才查",
        "无需读源码",
        "final_ready=true",
        "旧编号与结论失效、问题保留",
        "旧记忆和案例不得覆盖",
        "学习与复盘按各自模式组织",
    )
    for phrase in required_core_contract:
        if phrase not in skill_text:
            failures.append(f"SKILL.md missing current core contract: {phrase}")

    metadata = manifest["metadata"]
    normal_entrypoint = metadata.get("normal_entrypoint")
    workflow_text = read(root, normal_entrypoint) if normal_entrypoint and (root / normal_entrypoint).is_file() else ""
    workflow_contracts = {
        "exactly three manuscript modes": metadata.get("primary_artifacts") == list(expected_contracts),
        "one selected mode is mandatory": metadata.get("mandatory_runtime_context") == ["SKILL.md", "exactly-one-selected-references/modes-contract"],
        "unified entrypoint declared": normal_entrypoint == "scripts/transcript_task.py" and normal_entrypoint in skill_text,
        "three normal commands documented": all(f"transcript_task.py {name}" in skill_text for name in ("prepare", "review", "deliver")),
        "three normal commands implemented": all(f'commands.add_parser("{name}"' in workflow_text for name in ("prepare", "review", "deliver")),
        "local destination is the manifest default": metadata.get("default_destination") == "local-markdown",
        "local destination is the documented default": "默认本地" in skill_text and "用户明确要求 Get 时才指定" in skill_text,
        "local destination is the CLI default": 'choices=("local", "get-new", "get-update"), default="local"' in workflow_text,
        "recordings support user-provided terminology": "`audio` / `recorder_audio` / `meeting`" in skill_text and "用户提供并确认了术语表" in skill_text,
        "speaker count is not identity evidence": any("人物标签与人数不作为声纹证据" in gate for gate in metadata.get("key_gates", [])),
        "review changes invalidate old approval": "旧编号与结论失效" in skill_text and "重跑不撤销结论" in skill_text,
        "model input separated from generated report": "review-input.json" in skill_text and "模型不编辑程序报告" in skill_text,
    }
    failures.extend(f"normal workflow contract failed: {name}" for name, passed in workflow_contracts.items() if not passed)

    removed_resources = (
        "references/workflow-detailed.md",
        "references/delivery-modes.md",
        "references/exchange-transcript-qa.md",
        "references/experience-notes.md",
        "references/refine-method.md",
        "references/sentence-check.md",
        "references/sales-review-transcripts.md",
        "reports/output-risk-profile.md",
        "data/error-mapping.json",
    )
    active_instruction_text = skill_text + "\n" + "\n".join(
        read(root, relative) for relative in (*expected_contracts.values(), "references/scripts-guide.md")
    )
    for relative in removed_resources:
        if (root / relative).exists():
            failures.append(f"retired resource still active: {relative}")
        if relative in active_instruction_text:
            failures.append(f"active instruction still references retired resource: {relative}")

    exchange_contract = read(root, "references/modes/exchange.md")
    required_light_contract = (
        "轻度精炼（默认）",
        "中度精炼",
        "重度整理（仍非摘要）",
        "禁止跨时间搬运",
        "语义话轮",
        "不得先写主题摘要再回填对话",
        "由 review 提取原文完整问答和成稿对照",
    )
    for phrase in required_light_contract:
        if phrase not in exchange_contract:
            failures.append(f"exchange light-calibration contract missing: {phrase}")
    for case in cases["routing_cases"]:
        actual = classify(case["input"])
        if actual != case["expected"]:
            failures.append(f"route {case['input']!r}: expected {case['expected']}, got {actual}")
    for case in cases["intensity_cases"]:
        actual = classify_exchange_intensity(case["input"])
        if actual != case["expected"]:
            failures.append(f"intensity {case['input']!r}: expected {case['expected']}, got {actual}")
    for case in cases["retention_metric_cases"]:
        ratio, warning = retention_warning("稿" * case["draft_chars"], "源" * case["source_chars"])
        expected_ratio = case["draft_chars"] / case["source_chars"]
        if warning is not None or abs(ratio - expected_ratio) > 1e-12:
            failures.append(f"retention metric affected quality for {case['draft_chars']}/{case['source_chars']}")
    for case in cases["length_cases"]:
        if needs_split_proposal(case["source_chars"]) != case["expected_split_proposal"]:
            failures.append(f"long-source split threshold regression: {case}")
    for case in cases["multi_source_cases"]:
        actual = needs_multi_source_branch(case["fact_source_count"], case["explicit_merge"])
        if actual != case["expected"]:
            failures.append(f"multi-source branch regression: {case}")

    learning_source = read(root, quality["learning"]["source"])
    learning_good = analyze("learning", read(root, quality["learning"]["good"]), learning_source)
    learning_bad = analyze("learning", read(root, quality["learning"]["bad"]), learning_source)
    review_source = read(root, quality["review"]["source"])
    review_good = analyze("review", read(root, quality["review"]["good"]), review_source)
    review_bad = analyze("review", read(root, quality["review"]["bad"]), review_source)
    exchange_good = analyze("exchange", read(root, quality["exchange"]["good"]), read(root, quality["exchange"]["source"]), 600)
    fidelity_source = read(root, quality["exchange"]["fidelity_source"])
    fidelity_good = analyze("exchange", read(root, quality["exchange"]["fidelity_good"]), fidelity_source)
    fidelity_bad_detection = {
        relative: not analyze("exchange", read(root, relative), fidelity_source)["ok"]
        for relative in quality["exchange"].get("fidelity_bad", [])
    }
    fidelity_good_text = read(root, quality["exchange"]["fidelity_good"])
    fidelity_review_good = json.loads(read(root, quality["exchange"]["fidelity_review_good"]))
    fidelity_good_final = analyze(
        "exchange",
        fidelity_good_text,
        fidelity_source,
        final_check=True,
        review_report=fidelity_review_good,
    )
    # These old fixtures explicitly submit result=fail; they test blocking, not semantic detection.
    fidelity_bad_final_ready = {}
    for relative in quality["exchange"].get("fidelity_bad", []):
        bad_text = read(root, relative)
        bad_report = make_review_report("exchange", fidelity_source, bad_text, result="fail")
        fidelity_bad_final_ready[relative] = analyze(
            "exchange",
            bad_text,
            fidelity_source,
            final_check=True,
            review_report=bad_report,
        )["final_ready"]
    fake_overcompressed_review = make_fake_review_report(
        "exchange",
        fidelity_source,
        read(root, "evals/sample-exchange-fidelity-overcompressed.md"),
    )
    fake_overcompressed_ready = analyze(
        "exchange",
        read(root, "evals/sample-exchange-fidelity-overcompressed.md"),
        fidelity_source,
        final_check=True,
        review_report=fake_overcompressed_review,
    )["final_ready"]
    missing_review = analyze("exchange", fidelity_good_text, fidelity_source, final_check=True)
    mismatch_report = make_review_report("exchange", fidelity_source, fidelity_good_text)
    mismatch_report["draft_sha256"] = "0" * 64
    hash_mismatch = analyze(
        "exchange",
        fidelity_good_text,
        fidelity_source,
        final_check=True,
        review_report=mismatch_report,
    )
    unresolved_report = make_review_report("exchange", fidelity_source, fidelity_good_text)
    unresolved_report["samples"][0]["unresolved_items"] = ["开头仍有未解决检查项"]
    unresolved_review = analyze(
        "exchange",
        fidelity_good_text,
        fidelity_source,
        final_check=True,
        review_report=unresolved_report,
    )
    risk_lie_report = json.loads(json.dumps(fidelity_review_good, ensure_ascii=False))
    risk_lie_report["risk_checks"][0] = {
        "kind": "numbers_prices",
        "status": "not_present",
        "blocking_issues": [],
    }
    risk_lie = analyze(
        "exchange",
        fidelity_good_text,
        fidelity_source,
        final_check=True,
        review_report=risk_lie_report,
    )
    irrelevant_risk_report = json.loads(json.dumps(fidelity_review_good, ensure_ascii=False))
    irrelevant_risk_report["risk_checks"][0]["source_quotes"] = ["您好，我们提供图书目录整理服务"]
    irrelevant_risk_report["risk_checks"][0]["draft_quotes"] = ["您好，我们提供图书目录整理服务"]
    irrelevant_risk = analyze(
        "exchange",
        fidelity_good_text,
        fidelity_source,
        final_check=True,
        review_report=irrelevant_risk_report,
    )
    meta_narration = analyze(
        "exchange",
        "# 实录\n\n## 项目沟通 [00:00-00:10]\n\n**客户：** 随后沟通了一项临时项目。",
        "🟢 客户 [00:00]\n我们随后又谈到一个临时项目。",
    )
    plain_meta_narration = analyze(
        "exchange",
        "# 实录\n\n## 项目沟通 [00:00-00:10]\n\n客户认为这项业务不适合。\n\n**客户：** 我觉得这项业务不适合。",
        "🟢 客户 [00:00]\n我觉得这项业务不适合。",
    )
    untimed_source = "甲：这个方案我觉得还要再看看，明天 10:30 再开会。\n乙：好，我们继续讨论。"
    untimed_good = analyze("exchange", "# 交流实录\n\n## 方案讨论\n\n**甲：** 这个方案我觉得还要再看看，明天 10:30 再开会。\n\n**乙：** 好，我们继续讨论。", untimed_source)
    untimed_invented = analyze("exchange", "# 交流实录\n\n## 方案讨论 [00:00-00:30]\n\n**甲：** 这个方案我觉得还要再看看，明天 10:30 再开会。", untimed_source)
    for label, expected in (
        ("learning good", learning_good["ok"]),
        ("review good", review_good["ok"]),
        ("exchange good", exchange_good["ok"]),
        ("fidelity good", fidelity_good["ok"]),
    ):
        if not expected:
            failures.append(f"{label} fixture failed")
    if learning_bad["ok"]:
        failures.append("bad learning fixture unexpectedly passed")
    if review_bad["ok"]:
        failures.append("bad review fixture unexpectedly passed")
    if not untimed_good["ok"] or untimed_invented["ok"]:
        failures.append("untimed exchange heading policy regression")
    if not fidelity_good_final["final_ready"]:
        failures.append("good fidelity fixture did not become final_ready")
    if fidelity_good_final.get("validation_report_contract") != "transcript-refine-v3-final-ready-v1":
        failures.append("final validation report contract marker missing")
    if any(fidelity_bad_final_ready.values()):
        failures.append("bad fidelity fixture unexpectedly became final_ready")
    if fake_overcompressed_ready:
        failures.append("fake review report made overcompressed exchange final_ready")
    if missing_review["final_ready"] or hash_mismatch["final_ready"] or unresolved_review["final_ready"]:
        failures.append("final check accepted missing, mismatched or unresolved review report")
    if risk_lie["final_ready"]:
        failures.append("final check accepted not_present for a detected source risk")
    if irrelevant_risk["final_ready"]:
        failures.append("final check accepted irrelevant quotes for a checked risk")
    if meta_narration["ok"]:
        failures.append("editor narration inside speaker block was not rejected")
    if plain_meta_narration["ok"]:
        failures.append("third-person summary outside speaker blocks was not rejected")
    for label, report in (
        ("learning good", learning_good),
        ("review good", review_good),
        ("exchange good", exchange_good),
    ):
        if report.get("validation_scope") != "structure_and_traceability" or report.get("semantic_review_required") is not True:
            failures.append(f"{label} overstated deterministic validation scope")

    speaker_map = json.loads(read(root, "evals/sample-speaker-map-low-confidence.json"))
    map_invalidated = json.loads(read(root, "evals/sample-speaker-map-invalidated.json"))
    v2_mixed_text = read(root, "evals/sample-speaker-map-v2-mixed.json")
    v2_mixed = json.loads(v2_mixed_text)
    v2_empty = json.loads(read(root, "evals/sample-speaker-map-v2-empty-candidates.json"))
    v2_overlap = json.loads(read(root, "evals/sample-speaker-map-v2-overlap.json"))
    low_confidence = analyze("exchange", read(root, "evals/sample-exchange-low-confidence.md"), speaker_map=speaker_map, people_count=3, asr_channel_count=2)
    five_people_stable = analyze("exchange", read(root, "evals/sample-exchange-low-confidence.md"), people_count=5, asr_channel_count=2)
    two_person_map = json.loads(json.dumps(speaker_map, ensure_ascii=False))
    two_person_map["people_count"] = 2
    two_person_map["asr_channel_count"] = 1
    two_people_unstable = analyze(
        "exchange",
        read(root, "evals/sample-exchange-low-confidence.md"),
        speaker_map=two_person_map,
        people_count=2,
        asr_channel_count=1,
    )
    guessed_name = analyze("exchange", read(root, "evals/sample-exchange-guessed-name.md"), speaker_map=speaker_map, people_count=3, asr_channel_count=2)
    invalidated = analyze("exchange", read(root, "evals/sample-exchange-low-confidence.md"), speaker_map=map_invalidated, people_count=3, asr_channel_count=2)
    wave_appendix = analyze("exchange", read(root, "evals/sample-exchange-wave-appendix.md"), speaker_map=speaker_map, people_count=3, asr_channel_count=2)
    inline_time = analyze("exchange", read(root, "evals/sample-exchange-inline-time.md"), speaker_map=speaker_map, people_count=3, asr_channel_count=2)
    low_confidence_text = read(root, "evals/sample-exchange-low-confidence.md")
    v2_source = (
        "说话人1 [00:00]\n我们先确认现状，不先承诺结果。\n\n"
        "说话人1 [01:30]\n可以，先看已有材料。"
    )
    v2_review = make_review_report("exchange", v2_source, low_confidence_text, speaker_map_text=v2_mixed_text)
    v2_review["risk_checks"] = [
        {"kind": "numbers_prices", "status": "not_present", "blocking_issues": []},
        {"kind": "questions_objections", "status": "not_present", "blocking_issues": []},
        {
            "kind": "commitments_next_steps",
            "status": "checked",
            "source_locators": ["00:00"],
            "draft_locators": ["需求与边界"],
            "source_quotes": ["我们先确认现状，不先承诺结果。"],
            "draft_quotes": ["我们先确认现状，不先承诺结果。"],
            "preserved_items": ["不先承诺结果"],
            "blocking_issues": [],
        },
        {
            "kind": "speaker_asr_uncertainty",
            "status": "checked",
            "source_locators": ["00:00"],
            "draft_locators": ["需求与边界"],
            "source_quotes": ["说话人1 [00:00]"],
            "draft_quotes": ["说话人A"],
            "preserved_items": ["低置信人物标签"],
            "blocking_issues": [],
        },
        {"kind": "technical_interlude", "status": "not_present", "blocking_issues": []},
    ]
    v2_final = analyze(
        "exchange",
        low_confidence_text,
        v2_source,
        speaker_map=v2_mixed,
        people_count=2,
        asr_channel_count=1,
        speaker_state="unstable",
        final_check=True,
        review_report=v2_review,
        speaker_map_sha256=text_sha256(v2_mixed_text),
    )
    unstable_without_map = analyze("exchange", low_confidence_text, v2_source, speaker_state="unstable")
    empty_candidates = analyze("exchange", low_confidence_text, v2_source, speaker_map=v2_empty, speaker_state="unstable")
    overlapping_spans = analyze("exchange", low_confidence_text, v2_source, speaker_map=v2_overlap, speaker_state="unstable")
    expectations = {
        "low-confidence speaker map": low_confidence["ok"],
        "five people do not require a map by count": five_people_stable["ok"],
        "two-person unstable attribution accepts evidence map": two_people_unstable["ok"],
        "guessed unresolved name rejected": not guessed_name["ok"],
        "invalidated speaker map rejected": not invalidated["ok"],
        "wave separator and appendix accepted": wave_appendix["ok"],
        "inline timestamp rejected": not inline_time["ok"],
        "segmented v2 speaker map becomes final_ready": v2_final["final_ready"],
        "unstable attribution without map rejected": not unstable_without_map["ok"],
        "empty unresolved candidates rejected": not empty_candidates["ok"],
        "overlapping speaker spans rejected": not overlapping_spans["ok"],
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
        ok = (
            "os.remove" not in text
            and "--no-cleanup" in text
            and "--json" in text
            and "verify_readback" in text
            and "--validation-report" in text
            and "require_final_validation" in text
        )
        script_contracts[name] = ok
        if not ok:
            failures.append(f"save-script contract failed: {name}")
    refine_text = read(root, "scripts/refine-transcript.py")
    if "resolve_format" in refine_text or 'parser.error("创建模式需要由Skill明确传入 --format")' not in refine_text:
        failures.append("refine-transcript must not route manuscript type")

    risk_evidence_checks = risk_evidence_regressions()
    failures.extend("risk evidence regression: " + name for name, passed in risk_evidence_checks.items() if not passed)
    report = {
        "ok": not failures,
        "ok_scope": "deterministic structure_and_traceability regression gates only",
        "validation_scope": "structure_and_traceability",
        "semantic_review_required": True,
        "semantic_acceptance_status": "source_aligned_review_required",
        "routing_cases": len(cases["routing_cases"]),
        "intensity_cases": len(cases["intensity_cases"]),
        "retention_metric_cases": len(cases["retention_metric_cases"]),
        "length_cases": len(cases["length_cases"]),
        "multi_source_cases": len(cases["multi_source_cases"]),
        "missing_source_rejected": missing_source_rejected,
        "utf8_sig_final_check": bom_final_check_accepted,
        "branch_contracts": len(expected_contracts),
        "normal_workflow_contracts": workflow_contracts,
        "mode_quality": {"learning_good": learning_good["ok"], "learning_bad_rejected": not learning_bad["ok"], "review_good": review_good["ok"], "review_bad_rejected": not review_bad["ok"], "exchange_good": exchange_good["ok"], "untimed_exchange_good": untimed_good["ok"], "untimed_exchange_invented_time_rejected": not untimed_invented["ok"]},
        "speaker_lock": expectations,
        "read_back": verify_good["read_back"] == "verified" and verify_bad["read_back"] == "mismatch",
        "save_scripts": script_contracts,
        "semantic_fidelity": {
            "automated_gate": False,
            "diagnostic_only": True,
            "retention_is_quality_gate": False,
            "registered_bad_fixtures_rejected_by_structural_validator": fidelity_bad_detection,
            "registered_bad_fixtures_final_ready": fidelity_bad_final_ready,
            "registered_bad_fixtures_scope": "explicit failed-review blocking; not automated semantic detection",
            "fake_overcompressed_review_final_ready": fake_overcompressed_ready,
            "source_aligned_review_required": True,
            "good_fixture_final_ready": fidelity_good_final["final_ready"],
        },
        "risk_evidence_checks": risk_evidence_checks,
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
