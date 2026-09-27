"""4.1.1 contract tests. Submitted verdicts are fixtures, never semantic discovery."""
import copy
import json
import unittest
from unittest.mock import patch

import transcript_task as task
import review_workspace as reviews
import test_transcript_task as legacy


class ReviewWorkspaceTests(unittest.TestCase):
    setUp = legacy.WorkflowTests.setUp
    args = legacy.WorkflowTests.args
    select = legacy.WorkflowTests.select
    review = legacy.WorkflowTests.review
    deliver = legacy.WorkflowTests.deliver
    setup_ready = legacy.WorkflowTests.setup_ready

    def prepare(self, mode="exchange", **extra):
        argv = ["prepare", "--task-dir", str(self.directory), "--mode", mode, "--source-file", str(self.source)]
        for key, value in extra.items():
            argv += ["--" + key.replace("_", "-"), str(value)]
        result = task.prepare(self.args(*argv))
        # These cases exercise the retained pre-4.1.2 compatibility contract.
        manifest = task.inputs.read_json(self.directory / "task.json")
        manifest["skill_version"] = "4.1.1"
        manifest["exchange_metadata_required"] = False
        task.save_json(self.directory / "task.json", manifest)
        return result

    def report(self):
        return task.inputs.read_json(self.directory / "review-input.json")

    def save_report(self, form):
        task.save_json(self.directory / "review-input.json", form)

    def finish_report(self):
        legacy.WorkflowTests.finish_report(self)
        form = self.report()
        form["review_revision"] = task.inputs.read_json(self.directory / "task.json")["review_revision"]
        self.save_report(form)

    def all_bytes(self):
        return {str(p): p.read_bytes() for p in self.directory.rglob("*") if p.is_file()}

    def test_complete_all_modes_input_never_written_by_tools(self):
        for mode in ("exchange", "review", "learning"):
            self.directory = self.root / mode
            self.setup_ready(mode)
            before = (self.directory / "review-input.json").read_bytes()
            self.review()
            result = task.deliver(self.args("deliver", "--task", str(self.directory), "--output", str(self.root / (mode + ".md"))))
            self.assertTrue(result["ok"], result)
            self.assertEqual(before, (self.directory / "review-input.json").read_bytes())
            self.assertEqual((self.directory / "draft.md").read_bytes(), (self.root / (mode + ".md")).read_bytes())

    def test_input_pending_is_not_completed_by_review(self):
        self.setup_ready()
        form = self.report()
        form["result"] = "pending"
        form["samples"][0]["status"] = "pending"
        self.save_report(form)
        self.review()
        self.assertFalse(self.deliver()["ok"])

    def test_forged_generated_report_rejected_by_review_and_deliver(self):
        self.setup_ready()
        p = self.directory / "review-report.json"
        value = task.inputs.read_json(p)
        value["result"] = "pass"
        task.save_json(p, value)
        for operation in (self.review, self.deliver):
            with self.assertRaisesRegex(task.Error, "已被改写"):
                operation()

    def test_missing_input_not_legacy_fallback(self):
        self.setup_ready()
        (self.directory / "review-input.json").rename(self.directory / "input-away.json")
        with self.assertRaises(OSError):
            self.deliver()
        manifest = task.inputs.read_json(self.directory / "task.json")
        manifest.pop("review_input")
        task.save_json(self.directory / "task.json", manifest)
        with self.assertRaisesRegex(task.Error, "不能退回"):
            self.review()

    def test_concurrent_generated_report_edit_is_not_overwritten(self):
        self.setup_ready()
        original = task.analyze_task
        path = self.directory / "review-report.json"

        def edit_during_review(*args, **kwargs):
            report = task.inputs.read_json(path)
            report["blocking_issues"] = ["并发操作记录的问题"]
            task.save_json(path, report)
            return original(*args, **kwargs)

        with patch.object(task, "analyze_task", edit_during_review):
            with self.assertRaisesRegex(task.Error, "已被改写"):
                self.review()
        self.assertEqual(["并发操作记录的问题"], task.inputs.read_json(path)["blocking_issues"])

    def test_old_revision_rejected_after_draft_change(self):
        self.setup_ready()
        with (self.directory / "draft.md").open("a", encoding="utf-8") as h:
            h.write("\n")
        result = self.review()
        self.assertTrue(result["prior_completion_invalidated"])
        with self.assertRaisesRegex(task.Error, "旧编号"):
            self.deliver()

    def test_repeated_review_same_input_keeps_completed_status(self):
        self.setup_ready()
        self.assertTrue(self.deliver()["ok"])
        prior = task.inputs.read_json(self.directory / "task.json")["review_revision"]
        result = self.review()
        self.assertFalse(result["prior_completion_invalidated"])
        self.assertEqual(prior, result["review_revision"])
        self.assertEqual("pass", task.inputs.read_json(self.directory / "review-report.json")["result"])

    def test_locator_change_is_not_accepted_at_deliver(self):
        self.setup_ready()
        form = self.report()
        form["samples"][0]["source_locator"]["end"] -= 1
        self.save_report(form)
        with self.assertRaisesRegex(task.Error, "已变化"):
            self.deliver()

    def test_manual_quotes_rejected_in_model_input(self):
        self.setup_ready()
        form = self.report()
        form["samples"][0]["source_quote"] = "伪造引文"
        self.save_report(form)
        with self.assertRaisesRegex(task.Error, "手写引文"):
            self.review()

    def test_missing_duplicate_kind_rejected(self):
        self.setup_ready()
        form = self.report()
        form["risk_checks"][0]["kind"] = form["risk_checks"][1]["kind"]
        self.save_report(form)
        with self.assertRaisesRegex(task.Error, "重复"):
            self.review()

    def test_clear_issue_without_handling_rejected(self):
        self.setup_ready()
        form = self.report()
        form["samples"][0]["blocking_issues"] = ["行动人待核对"]
        self.save_report(form)
        self.review()
        form["samples"][0]["blocking_issues"] = []
        self.save_report(form)
        self.review()  # Even another review may not erase the previous issue.
        with self.assertRaisesRegex(task.Error, "逐条处置"):
            self.deliver()

    def test_handled_issue_keeps_history(self):
        self.setup_ready()
        form = self.report()
        form["samples"][0]["unresolved_items"] = ["行动人待核对"]
        self.save_report(form)
        self.review()
        form["samples"][0]["unresolved_items"] = []
        form["samples"][0]["resolved_issues"] = [{"issue": "行动人待核对", "handling": "测试夹具：核对原文和正文同一位置，保持客户身份，不声称模型自动发现。"}]
        self.save_report(form)
        self.assertTrue(self.deliver()["ok"])
        stored = task.inputs.read_json(self.directory / "review-report.json")
        self.assertIn("行动人待核对", str(stored["samples"][0]["resolved_issues"]))

    def test_unknown_or_empty_handling_rejected(self):
        self.setup_ready()
        form = self.report()
        form["resolved_issues"] = [{"issue": "不存在的问题", "handling": "说明"}]
        self.save_report(form)
        with self.assertRaisesRegex(task.Error, "未对应"):
            self.deliver()
        form["resolved_issues"][0]["handling"] = ""
        self.save_report(form)
        with self.assertRaisesRegex(task.Error, "具体处置"):
            self.deliver()

    def test_read_only_pages_diff_and_out_of_range(self):
        self.setup_ready()
        before = self.all_bytes()
        for flags in (("--page", "1"), ("--full-diff",), ("--page", "1", "--full-diff")):
            result = self.review(*flags)
            self.assertTrue(result["read_only"])
            self.assertTrue(result["text"])
            self.assertEqual(before, self.all_bytes())
        with self.assertRaisesRegex(task.Error, "页码越界"):
            self.review("--page", "999")
        self.assertEqual(before, self.all_bytes())

    def test_page_cannot_change_parameters(self):
        self.setup_ready()
        before = self.all_bytes()
        with self.assertRaisesRegex(task.Error, "只读查看"):
            self.review("--page", "1", "--speaker-state", "unstable")
        self.assertEqual(before, self.all_bytes())

    def test_source_and_selector_bounds(self):
        self.setup_ready()
        form = self.report()
        form["samples"][0]["source_locator"]["end"] = 100000
        self.save_report(form)
        with self.assertRaises(task.Error):
            self.review()
        with (self.directory / "source/source.txt").open("a", encoding="utf-8") as h:
            h.write("改变原文")
        with self.assertRaisesRegex(task.Error, "事实源已变化"):
            self.review()

    def test_input_alias_output_protected(self):
        self.setup_ready()
        with self.assertRaisesRegex(task.Error, "重合"):
            task.deliver(self.args("deliver", "--task", str(self.directory), "--output", str(self.directory / "review-input.json"), "--replace"))

    def test_input_concurrent_change_during_review(self):
        self.setup_ready()
        original = task.analyze_task
        def race(*args, **kwargs):
            result = original(*args, **kwargs)
            form = self.report()
            form["blocking_issues"] = ["其他编辑提出的新问题"]
            self.save_report(form)
            return result
        with patch.object(task, "analyze_task", side_effect=race), self.assertRaisesRegex(task.Error, "输入已变化"):
            self.review()
        self.assertEqual(["其他编辑提出的新问题"], self.report()["blocking_issues"])

    def test_input_concurrent_change_before_save(self):
        self.setup_ready()
        original = task.require_final_validation
        def race(*args, **kwargs):
            result = original(*args, **kwargs)
            form = self.report()
            form["blocking_issues"] = ["保存前新增问题"]
            self.save_report(form)
            return result
        with patch.object(task, "require_final_validation", side_effect=race), self.assertRaisesRegex(task.Error, "输入发生变化"):
            self.deliver()
        self.assertFalse((self.root / "final.md").exists())

    def test_invalid_prepare_creates_no_directory(self):
        raw = self.root / "wrong.json"
        task.save_json(raw, {"id": "222", "transcript": "原文"})
        with self.assertRaises(task.Error):
            task.prepare(self.args("prepare", "--task-dir", str(self.directory), "--mode", "exchange", "--note-id", "111", "--response-file", str(raw)))
        self.assertFalse(self.directory.exists())

    def test_inline_log_preparation_reuses_exact_response(self):
        log = self.root / "session.jsonl"
        content = "🟢 说话人1 [00:01]\n真实原文，不能重抄。\n"
        rows = [{"type": "function_call", "callId": "abc", "name": "DeferExecuteTool", "arguments": json.dumps({"toolName": "mcp__getnote__get_note_transcript", "params": {"id": "123"}})},
                {"type": "function_call_result", "callId": "abc", "output": {"type": "text", "text": json.dumps({"id": "123", "transcript": content}, ensure_ascii=False)}}]
        log.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")
        result = task.prepare(self.args("prepare", "--task-dir", str(self.directory), "--mode", "exchange", "--note-id", "123", "--response-file", str(log), "--call-id", "abc"))
        self.assertTrue(result["ok"])
        self.assertEqual(content.encode(), (self.directory / "source/source.txt").read_bytes())
        with self.assertRaises(task.Error):
            task.prepare(self.args("prepare", "--task-dir", str(self.root / "bad"), "--mode", "exchange", "--note-id", "123", "--response-file", str(log), "--call-id", "missing"))
        self.assertFalse((self.root / "bad").exists())

    def test_compact_dedup_and_appendix_included(self):
        self.setup_ready()
        with (self.directory / "draft.md").open("a", encoding="utf-8") as h:
            h.write("\n## 沟通要点附录\n唯一附录内容需要核对。\n")
        result = self.review()
        text = (self.directory / "review.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("唯一附录内容需要核对。"), 1)
        self.assertNotIn("~~~~diff", text)
        self.assertTrue(result["text"])

    def test_pagination_never_drops_lines_or_sections(self):
        sections = ["# 材料\n" + "\n\n".join(f"{i}: 内容" + "字" * 60 for i in range(900)), "## 最后一节\n结尾不可遗漏"]
        pages = reviews.paginate(sections)
        self.assertGreater(len(pages), 1)
        joined = "\n".join(pages)
        for i in range(900):
            self.assertIn(f"{i}: 内容", joined)
        self.assertIn("结尾不可遗漏", joined)
        self.assertTrue(all(len(x) <= 12000 for x in pages))

    def test_pagination_keeps_source_header_with_its_paragraph(self):
        fragment = "88: 说话人1 [00:01]\n89: " + "字" * 90
        pages = reviews.paginate(["# 材料\n1: 开头\n\n" + fragment], target=100)
        self.assertTrue(any(fragment in page for page in pages))
        self.assertEqual(sum(page.count(fragment) for page in pages), 1)

    def test_compact_retains_every_selected_nonblank_line(self):
        self.setup_ready()
        self.review()
        report = task.inputs.read_json(self.directory / "review-report.json")
        sections = reviews.compact_sections(report, self.source.read_text(encoding="utf-8"),
            (self.directory / "draft.md").read_text(encoding="utf-8"), {"ok": True}, 1)
        for side, content, label in (("source", self.source.read_text(encoding="utf-8"), "原文"),
                                     ("draft", (self.directory / "draft.md").read_text(encoding="utf-8"), "成稿")):
            shown = "\n".join(x for x in sections if x.startswith("## " + label))
            lines = content.splitlines()
            for start, end, _ in reviews.ranges_for(report, side):
                for n in range(start, end + 1):
                    if lines[n-1].strip():
                        self.assertIn(f"{n}: {lines[n-1]}", shown)


if __name__ == "__main__":
    unittest.main(verbosity=2)
