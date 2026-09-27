#!/usr/bin/env python3
"""Deterministic locator, appendix and save-contract regression; no network."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from note_io_common import require_final_validation, text_sha256
from validate_transcript_artifact import (
    REQUIRED_RISK_CHECKS,
    analyze,
    is_low_confidence_label,
    resolve_line_locator,
    validate_review_report,
    validate_speaker_map,
)


ROOT = Path(__file__).resolve().parents[1]


def lines(text):
    return {"kind": "line", "start": 1, "end": len(text.splitlines())}


def report_for(mode, source, draft):
    """Synthetic claimed-pass report; tests exercise mechanics, not semantics."""
    return {
        "version": 1,
        "mode": mode,
        "source_sha256": text_sha256(source),
        "draft_sha256": text_sha256(draft),
        "samples": [
            {
                "kind": kind,
                "source_locator": lines(source),
                "draft_locator": lines(draft),
                "source_quote": source,
                "draft_quote": draft,
                "preserved_items": ["测试用明确引文及位置"],
                "unresolved_items": [],
                "blocking_issues": [],
                "status": "pass",
            }
            for kind in ("opening", "middle", "ending")
        ],
        "risk_checks": [
            {
                "kind": kind,
                "status": "checked",
                "source_locators": [lines(source)],
                "draft_locators": [lines(draft)],
                "source_quotes": [source],
                "draft_quotes": [draft],
                "preserved_items": ["测试用位置与引文"],
                "semantic_reason": "此项为机械校验测试夹具，语义判断不是这项测试的验收对象。",
                "unresolved_items": [],
                "blocking_issues": [],
            }
            for kind in sorted(REQUIRED_RISK_CHECKS)
        ],
        "unresolved_items": [],
        "blocking_issues": [],
        "result": "pass",
    }


class LocatorTests(unittest.TestCase):
    def setUp(self):
        self.source = "第一段真实发言。\n第二段不同内容。"
        self.draft = "# 测试稿\n\n## 讨论\n\n**甲：** 第一段真实发言。\n\n**乙：** 第二段不同内容。"
        self.review = report_for("exchange", self.source, self.draft)

    def validate(self, review=None):
        return validate_review_report(review or self.review, "exchange", self.source, self.draft)

    def test_extract_inclusive_lines(self):
        self.assertEqual(resolve_line_locator("甲\r\n乙\r\n丙\r\n", {"kind": "line", "start": 2, "end": 3}), "乙\n丙")

    def test_invalid_ranges_fail(self):
        bad = [None, {}, "第1行", {"kind": "time", "start": 1, "end": 1}]
        for start, end in ((0, 1), (-1, 1), (2, 1), (1, 3), (True, 1), (1, False), (1.0, 2), ("1", 2)):
            bad.append({"kind": "line", "start": start, "end": end})
        for locator in bad:
            with self.subTest(locator=locator), self.assertRaises(ValueError):
                resolve_line_locator(self.source, locator)
        with self.assertRaises(ValueError):
            resolve_line_locator("正文\n  \n", {"kind": "line", "start": 2, "end": 2})
        with self.assertRaises(ValueError):
            resolve_line_locator("", {"kind": "line", "start": 1, "end": 1})

    def test_structured_report_records_verified_ranges(self):
        issues, stats = self.validate()
        self.assertEqual(issues, [])
        self.assertEqual(stats["review_locator_verification"], "structured_line_ranges")
        self.assertEqual(stats["review_locator_counts"]["structured_line"], 16)

    def test_sample_quote_elsewhere_in_source_fails(self):
        item = self.review["samples"][0]
        item["source_locator"] = {"kind": "line", "start": 2, "end": 2}
        item["source_quote"] = "第一段真实发言。"
        self.assertTrue(any("inside its line range" in x for x in self.validate()[0]))
        self.assertEqual(self.validate()[1]["review_locator_verification"], "invalid")

    def test_sample_quote_elsewhere_in_draft_fails(self):
        item = self.review["samples"][1]
        item["draft_locator"] = {"kind": "line", "start": 7, "end": 7}
        item["draft_quote"] = "第一段真实发言。"
        self.assertTrue(any("inside its line range" in x for x in self.validate()[0]))

    def test_every_risk_pair_is_checked(self):
        for side in ("source", "draft"):
            review = copy.deepcopy(self.review)
            item = review["risk_checks"][0]
            line1, line2 = (1, 2) if side == "source" else (5, 7)
            item[side + "_quotes"] = ["第一段真实发言。", "第二段不同内容。"]
            item[side + "_locators"] = [
                {"kind": "line", "start": line1, "end": line1},
                {"kind": "line", "start": line1, "end": line1},
            ]
            with self.subTest(side=side):
                self.assertTrue(any("inside its line range" in x for x in self.validate(review)[0]))
                item[side + "_locators"][1] = {"kind": "line", "start": line2, "end": line2}
                self.assertEqual(self.validate(review)[0], [])

    def test_structured_risk_counts_must_match(self):
        self.review["risk_checks"][0]["source_quotes"].append("第二段不同内容。")
        self.assertTrue(any("one locator for every quote" in x for x in self.validate()[0]))

    def test_mixed_risk_counts_must_match(self):
        self.review["risk_checks"][0]["source_locators"].append("旧定位")
        self.assertTrue(any("one locator for every quote" in x for x in self.validate()[0]))

    def test_structured_not_present_checks_optional_source_evidence(self):
        item = self.review["risk_checks"][0]
        item["status"] = "not_present"
        item["source_quotes"] = ["第一段真实发言。"]
        item["source_locators"] = [{"kind": "line", "start": 2, "end": 2}]
        item["draft_quotes"] = []
        item["draft_locators"] = []
        self.assertTrue(any("inside its line range" in x for x in self.validate()[0]))
        item["source_locators"][0]["start"] = 1
        self.assertEqual(self.validate()[0], [])

    def test_legacy_report_remains_compatible_and_explicit(self):
        for item in self.review["samples"]:
            item["source_locator"], item["draft_locator"] = "旧时间位置", "旧话题名"
        for item in self.review["risk_checks"]:
            item["source_locators"], item["draft_locators"] = ["旧时间位置"], ["旧话题名"]
        self.review["risk_checks"][0]["source_quotes"].append("第二段不同内容。")
        issues, stats = self.validate()
        self.assertEqual(issues, [])
        self.assertEqual(stats["review_locator_verification"], "legacy_free_text")
        self.assertIs(stats["legacy_locator_ranges_verified"], False)
        self.review["risk_checks"][0]["source_quotes"].append("没有说过的虚构内容。")
        self.assertTrue(self.validate()[0])

    def test_pass_claim_never_overrides_blockers_or_stale_hash(self):
        for field, value in (("blocking_issues", ["未解决的问题"]), ("source_sha256", "0" * 64), ("draft_sha256", "0" * 64)):
            with self.subTest(field=field):
                bad = copy.deepcopy(self.review)
                bad[field] = value
                self.assertFalse(analyze("exchange", self.draft, self.source, final_check=True, review_report=bad)["final_ready"])

    def test_three_modes_keep_their_own_structure(self):
        for mode, fixture in (("exchange", "sample-exchange.md"), ("review", "sample-review-good.md"), ("learning", "sample-learning-good.md")):
            with self.subTest(mode=mode):
                draft = (ROOT / "evals" / fixture).read_text(encoding="utf-8-sig")
                source = "这里是供机械校验测试使用的真实源内容。"
                report = analyze(mode, draft, source, final_check=True, review_report=report_for(mode, source, draft))
                self.assertTrue(report["final_ready"], report)
                self.assertEqual(report["final_ready_reason"], "review_report_traceability_complete")
                self.assertIs(report["program_semantics_verified"], False)

    def test_new_and_legacy_save_reasons_preserve_gates(self):
        validation = analyze("exchange", self.draft, self.source, final_check=True, review_report=self.review)
        validation.update(source_sha256=text_sha256(self.source), draft_sha256=text_sha256(self.draft), review_report_sha256=text_sha256(json.dumps(self.review)))
        temp_root = ROOT.parent / "checks"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="locator-save-", dir=temp_root) as folder:
            path = Path(folder) / "validation.json"
            for reason in ("review_report_traceability_complete", "source_aligned_review_complete"):
                validation["final_ready_reason"] = reason
                path.write_text(json.dumps(validation), encoding="utf-8")
                self.assertTrue(require_final_validation(str(path), self.draft)["final_ready"])
            for payload in (
                {"final_ready": True, "draft_sha256": text_sha256(self.draft), "mode": "exchange"},
                {**validation, "review_evidence_sha256": "0" * 64},
                {**validation, "review_evidence": None},
                {**validation, "final_issues": ["未解决"]},
            ):
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    require_final_validation(str(path), self.draft)
            path.write_text(json.dumps(validation), encoding="utf-8")
            with self.assertRaises(ValueError):
                require_final_validation(str(path), self.draft + "新增正文")


class SpeakerBodyTests(unittest.TestCase):
    def test_explicit_uncertain_roles_and_original_labels_are_allowed(self):
        for label in (
            "说话人A", "说话人2", "低置信B", "低置信说话人1",
            "接待者（低置信，原标签示例甲）", "说话人2（归属待核）",
            "接待者（低置信）", "接待者（归属待核）", "示例甲（低置信）",
        ):
            with self.subTest(label=label):
                self.assertTrue(is_low_confidence_label(label))
                mapping = self.uncertain_map(label)
                draft = f"# 实录\n\n## 讨论\n\n**{label}：** 当前原句仍保留。"
                self.assertEqual(validate_speaker_map(draft, mapping)[0], [])

    @staticmethod
    def uncertain_map(label):
        return {
            "version": 2, "people_count": 2, "asr_channel_count": 1,
            "mappings": [{
                "asr_label": "示例甲", "output_label": label, "person": None,
                "candidates": ["接待者", "提问者"], "confidence": "low", "status": "unresolved",
                "source_span": {"kind": "time", "start": "00:01", "end": "00:01"},
                "evidence": ["原标签示例甲；段内承担接待回应，但具体身份未证实。"],
            }],
        }

    def test_unmarked_names_empty_subjects_and_multiline_labels_are_rejected(self):
        for label in (
            "示例甲", "接待者", "（低置信）", "  （归属待核）",
            "接待者（低置信，原标签）", "接待者（低置信，原标签  ）",
            "接待\n者（低置信）", "接待者（低置信，原标签示例\r甲）",
            "说话人A\n", "接待者\u2028（归属待核）", "接待者（低置信）补充",
        ):
            with self.subTest(label=label):
                self.assertFalse(is_low_confidence_label(label))
                self.assertTrue(validate_speaker_map("# 实录", self.uncertain_map(label))[0])

    def test_explicit_uncertain_labels_do_not_relax_other_mapping_fields(self):
        label = "接待者（低置信，原标签示例甲）"
        draft = f"# 实录\n\n## 讨论\n\n**{label}：** 当前原句仍保留。"
        for field, value in (("person", "示例甲"), ("candidates", []), ("confidence", "high"), ("status", "unknown"), ("evidence", [])):
            with self.subTest(field=field):
                mapping = self.uncertain_map(label)
                mapping["mappings"][0][field] = value
                self.assertTrue(validate_speaker_map(draft, mapping)[0])

    def test_appendix_bold_heading_is_not_a_speaker(self):
        mapping = {
            "version": 2, "people_count": 2, "asr_channel_count": 1,
            "mappings": [
                {"asr_label": "说话人1", "output_label": "甲", "person": "甲", "evidence": ["测试中已确认"], "confidence": "high", "status": "locked", "source_span": {"kind": "time", "start": "00:01", "end": "00:01"}},
                {"asr_label": "说话人1", "output_label": "乙", "person": "乙", "evidence": ["测试中已确认"], "confidence": "high", "status": "locked", "source_span": {"kind": "time", "start": "00:01", "end": "00:01"}},
            ],
        }
        for heading in ("疑似识别错误与修正说明", "疑似识别错误与待确认项"):
            draft = f"# 实录\n\n## 讨论 [00:00-00:02]\n\n**甲：** 这里先保留原句。\n\n**乙：** 这是另一句。\n\n## {heading}\n\n**数字说明：** 有待回听。"
            with self.subTest(heading=heading):
                self.assertEqual(validate_speaker_map(draft, mapping)[0], [])
                self.assertTrue(analyze("exchange", draft, "[00:00]\n这里先保留原句。", speaker_map=mapping)["ok"])
        bad = draft.replace("**甲：**", "**丙：**")
        self.assertTrue(validate_speaker_map(bad, mapping)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
