#!/usr/bin/env python3
"""Offline tests of the unified workflow; model decisions below are fixtures, not semantic discovery."""

import argparse
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import transcript_task as task


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        artifact_root = os.environ.get("TRANSCRIPT_TEST_ARTIFACTS")
        if artifact_root:
            self.root = Path(artifact_root).resolve() / (self._testMethodName + "-" + uuid.uuid4().hex[:8])
            self.root.mkdir(parents=True)
        else:
            self.temp = tempfile.TemporaryDirectory()
            self.addCleanup(self.temp.cleanup)
            self.root = Path(self.temp.name)
        self.source = self.root / "original.txt"
        self.source.write_text("先了解客户的业务情况。\n服务一年三百块钱。\n价格需看条件，后面再联系。\n", encoding="utf-8")
        self.directory = self.root / "task"
        self.fixture = Path(__file__).resolve().parent.parent / "evals"

    def args(self, *argv):
        return task.parser().parse_args(list(argv))

    def prepare(self, mode="exchange", **extra):
        argv = ["prepare", "--task-dir", str(self.directory), "--mode", mode, "--source-file", str(self.source)]
        for name, value in extra.items():
            argv += ["--" + name.replace("_", "-"), str(value)]
        result = task.prepare(self.args(*argv))
        # Exercise the retained 4.1.0 task contract. New ownership has a separate suite.
        manifest = task.inputs.read_json(self.directory / "task.json")
        manifest["skill_version"] = "4.1.0"
        manifest["exchange_metadata_required"] = False
        for key in ("review_input", "review_revision", "generated_report_sha256"):
            manifest.pop(key, None)
        task.save_json(self.directory / "task.json", manifest)
        return result

    def report(self):
        return task.inputs.read_json(self.directory / "review-report.json")

    def save_report(self, report):
        task.save_json(self.directory / "review-report.json", report)

    def select(self):
        report = self.report()
        draft = task.read_text(self.directory / "draft.md")
        source = task.read_text(self.directory / "source/source.txt")
        s = {"kind": "line", "start": 1, "end": len(source.splitlines())}
        d = {"kind": "line", "start": 1, "end": len(draft.splitlines())}
        for item in report["samples"]:
            item["source_locator"], item["draft_locator"] = s.copy(), d.copy()
        for item in report["risk_checks"]:
            item["source_locators"], item["draft_locators"] = [s.copy()], [d.copy()]
        self.save_report(report)

    def finish_report(self):
        report = self.report()
        for item in report["samples"]:
            item.update(status="pass", preserved_items=["测试夹具已核对，不代表自动识别语义问题"])
        for item in report["risk_checks"]:
            item.update(status="checked", preserved_items=["测试夹具包含完整源文及成稿"],
                        semantic_reason="本项用完整文本作为离线接口测试证据，语义判断由测试夹具声明。")
        report["result"] = "pass"
        self.save_report(report)

    def review(self, *extra):
        return task.review(self.args("review", "--task", str(self.directory), *extra))

    def deliver(self, *extra):
        return task.deliver(self.args("deliver", "--task", str(self.directory), "--output", str(self.root / "final.md"), *extra))

    def setup_ready(self, mode="exchange"):
        if mode == "exchange":
            source_name, draft_name = "sample-exchange-fidelity-source.txt", "sample-exchange-fidelity-good.md"
        else:
            source_name, draft_name = "sample-" + mode + "-source.txt", "sample-" + mode + "-good.md"
        self.source.write_bytes((self.fixture / source_name).read_bytes())
        self.prepare(mode)
        (self.directory / "draft.md").write_bytes((self.fixture / draft_name).read_bytes())
        self.select()
        result = self.review()
        self.assertTrue(result["ok"], result)
        self.finish_report()

    def test_prepare_source_exact_and_pending_all_modes(self):
        for mode in ("exchange", "review", "learning"):
            with self.subTest(mode=mode):
                self.directory = self.root / mode
                self.assertTrue(self.prepare(mode)["ok"])
                self.assertEqual((self.directory / "source/source.txt").read_bytes(), self.source.read_bytes())
                report = self.report()
                self.assertEqual(report["result"], "pending")
                self.assertEqual(len(report["samples"]), 3)
                self.assertEqual(len(report["risk_checks"]), 5)
                self.assertTrue(all(i["status"] == "pending" for _, i in task.report_items(report)))

    def test_new_exchange_requires_traceability_header(self):
        self.source.write_text("[00:00] 原文\n", encoding="utf-8")
        args = self.args("prepare", "--task-dir", str(self.directory), "--mode", "exchange", "--source-file", str(self.source))
        self.assertTrue(task.prepare(args)["ok"])
        draft = self.directory / "draft.md"
        draft.write_text("# 测试｜交流实录稿\n\n## 话题 [00:00-00:01]\n\n**说话人A：** 原文\n", encoding="utf-8")
        manifest = task.load_task(self.directory)[1]
        issues = task.analyze_task(manifest)["issues"]
        self.assertTrue(any("exchange metadata missing required fields" in issue for issue in issues))
        draft.write_text(
            "# 测试｜交流实录稿\n\n> 会议时间：未提供\n> 参会人员：身份待核\n> 原始笔记：笔记ID-123（链接待补）\n\n"
            "## 话题 [00:00-00:01]\n\n**说话人A：** 原文\n",
            encoding="utf-8",
        )
        issues = task.analyze_task(manifest)["issues"]
        self.assertFalse(any("exchange metadata" in issue for issue in issues))

    def test_get_response_and_fetch_reuse(self):
        response = self.root / "real-response.json"
        payload = {"data": {"note": {"id": "1234567890123456789", "note_type": "meeting", "audio": {"original": self.source.read_text(encoding="utf-8")}}}}
        task.save_json(response, payload)
        args = self.args("prepare", "--task-dir", str(self.directory), "--mode", "exchange", "--note-id", "1234567890123456789", "--response-file", str(response))
        self.assertTrue(task.prepare(args)["ok"])
        saved = task.inputs.read_json(self.directory / "source/source-record.json")
        self.assertEqual(saved["note_id"], "1234567890123456789")
        self.directory = self.root / "fetch"
        args = self.args("prepare", "--task-dir", str(self.directory), "--mode", "learning", "--note-id", "1234567890123456789")
        with patch.object(task.inputs, "fetch_response", return_value=(task.inputs.json_bytes(payload), payload, {"method": "GET"})) as mock:
            self.assertTrue(task.prepare(args)["ok"])
            mock.assert_called_once()

    def test_wrong_get_id_no_source(self):
        response = self.root / "response.json"
        task.save_json(response, {"id": "456", "transcript": "真实内容不能换ID"})
        args = self.args("prepare", "--task-dir", str(self.directory), "--mode", "exchange", "--note-id", "123", "--response-file", str(response))
        with self.assertRaises(task.Error):
            task.prepare(args)
        self.assertFalse((self.directory / "source/source.txt").exists())

    def test_prepare_rejects_nonempty_directory(self):
        self.prepare()
        original = (self.directory / "task.json").read_bytes()
        with self.assertRaises(task.Error):
            self.prepare()
        self.assertEqual(original, (self.directory / "task.json").read_bytes())

    def test_long_source_choice_blocks_review(self):
        self.source.write_text("原文不能遗漏。" * 6000, encoding="utf-8")
        result = self.prepare()
        self.assertFalse(result["ok"])
        with self.assertRaisesRegex(task.Error, "长稿"):
            self.review()
        manifest = task.inputs.read_json(self.directory / "task.json")
        manifest["long_source_choice"] = "split-confirmed"
        with self.assertRaises(task.Error):
            task.check_long_source(manifest)
        manifest["long_source_choice"] = "keep-one"
        task.check_long_source(manifest)

    def test_show_lines_is_read_only(self):
        self.prepare()
        before = {p: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()}
        result = self.review("--show-lines", "source", "--start", "2", "--end", "3")
        self.assertIn("2: 服务一年", result["text"])
        self.assertEqual(before, {p: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()})

    def test_local_delivery_all_modes(self):
        for mode in ("exchange", "review", "learning"):
            with self.subTest(mode=mode):
                self.directory = self.root / mode
                self.setup_ready(mode)
                out = self.root / (mode + ".md")
                result = task.deliver(self.args("deliver", "--task", str(self.directory), "--output", str(out)))
                self.assertTrue(result["ok"], result)
                self.assertEqual(out.read_bytes(), (self.directory / "draft.md").read_bytes())
                final = task.inputs.read_json(self.directory / "validation-report.json")
                task.require_final_validation(str(self.directory / "validation-report.json"), task.read_text(out), mode)
                self.assertTrue(final["final_ready"])

    def test_review_never_marks_pass_and_keeps_issues(self):
        self.setup_ready()
        report = self.report()
        report["blocking_issues"] = ["价格仍需核对"]
        report["samples"][0]["unresolved_items"] = ["需补上下文"]
        report["samples"][0]["reason"] = "已有解释保留"
        self.save_report(report)
        with (self.directory / "draft.md").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        result = self.review()
        self.assertTrue(result["prior_completion_invalidated"])
        report = self.report()
        self.assertEqual(report["result"], "pending")
        self.assertEqual(report["blocking_issues"], ["价格仍需核对"])
        self.assertEqual(report["samples"][0]["unresolved_items"], ["需补上下文"])
        self.assertEqual(report["samples"][0]["reason"], "已有解释保留")
        self.assertFalse(self.deliver()["ok"])
        self.assertFalse((self.root / "final.md").exists())

    def test_changed_draft_rejected_before_hash_update(self):
        self.setup_ready()
        before = (self.directory / "review-report.json").read_bytes()
        with (self.directory / "draft.md").open("a", encoding="utf-8") as handle:
            handle.write("\n新增未经复核的一句。")
        with self.assertRaisesRegex(task.Error, "已变化"):
            self.deliver()
        self.assertEqual(before, (self.directory / "review-report.json").read_bytes())

    def test_source_changed_fails_even_review(self):
        self.setup_ready()
        with (self.directory / "source/source.txt").open("a", encoding="utf-8") as handle:
            handle.write("伪造结尾")
        with self.assertRaisesRegex(task.Error, "事实源已变化"):
            self.review()

    def test_changed_selector_rejected(self):
        self.setup_ready()
        report = self.report()
        report["samples"][0]["source_locator"]["end"] -= 1
        self.save_report(report)
        with self.assertRaisesRegex(task.Error, "已变化"):
            self.deliver()
        self.review()
        self.assertEqual(self.report()["result"], "pending")

    def test_changed_speaker_map_bytes_rejected(self):
        self.setup_ready()
        mapping = self.root / "speaker-map.json"
        task.save_json(mapping, {"version": 1, "people_count": 2, "asr_channel_count": 2,
            "mappings": [{"asr_label": "说话人1", "output_label": "销售", "person": "销售", "status": "locked", "confidence": "high", "evidence": ["测试夹具角色"]},
                         {"asr_label": "说话人2", "output_label": "客户", "person": "客户", "status": "locked", "confidence": "high", "evidence": ["测试夹具角色"]}]})
        self.review("--speaker-map", str(mapping))
        self.finish_report()
        before = (self.directory / "review-report.json").read_bytes()
        mapping.write_bytes(mapping.read_bytes() + b"\n")
        with self.assertRaisesRegex(task.Error, "已变化"):
            self.deliver()
        self.assertEqual(before, (self.directory / "review-report.json").read_bytes())

    def test_speaker_map_cannot_alias_generated_outputs(self):
        self.setup_ready()
        structure = self.directory / "structure.json"
        before = structure.read_bytes()
        with self.assertRaises(task.Error):
            self.review("--speaker-map", str(structure))
        self.assertEqual(structure.read_bytes(), before)
        alias = self.root / "map-hardlink.json"
        os.link(structure, alias)
        with self.assertRaises(task.Error):
            self.review("--speaker-map", str(alias))

    def test_custom_get_configuration_path_reused_without_copying_credentials(self):
        self.setup_ready()
        config = self.root / "get-config.json"
        task.save_json(config, {"api_key": "fixture-secret", "client_id": "fixture-client"})
        manifest = task.inputs.read_json(self.directory / "task.json")
        manifest["get_config_path"] = str(config)
        task.save_json(self.directory / "task.json", manifest)
        draft = task.read_text(self.directory / "draft.md")
        module = SimpleNamespace(load_config=Mock(side_effect=AssertionError("must not switch to default credentials")),
            get_note_detail=Mock(side_effect=[{"title": "原笔记"}, {"title": "校正标题", "content": draft}]),
            build_new_note_content=lambda draft, original, scene, fmt: draft,
            create_note=Mock(return_value={"code": 0, "data": {"id": "999"}}))
        with patch.object(task, "load_save_module", return_value=module):
            result = self.deliver("--destination", "get-new", "--source-note-id", "123", "--output-title", "校正标题")
        self.assertTrue(result["ok"])
        self.assertEqual(module.create_note.call_args.args[0]["api_key"], "fixture-secret")
        self.assertNotIn("fixture-secret", (self.directory / "task.json").read_text(encoding="utf-8"))
        with self.assertRaises(task.Error):
            task.deliver(self.args("deliver", "--task", str(self.directory), "--output", str(config), "--replace"))

    def test_review_reextracting_altered_quotes_invalidates_pass(self):
        self.setup_ready()
        report = self.report()
        report["samples"][0]["source_quote"] = "手写错误引文"
        self.save_report(report)
        result = self.review()
        self.assertTrue(result["prior_completion_invalidated"])
        self.assertEqual(self.report()["result"], "pending")
        self.assertNotEqual(self.report()["samples"][0]["source_quote"], "手写错误引文")

    def test_quote_existing_elsewhere_not_accepted_in_selected_range(self):
        self.setup_ready()
        report = self.report()
        report["samples"][0]["source_locator"] = {"kind": "line", "start": 1, "end": 1}
        self.save_report(report)
        self.review()
        self.finish_report()
        report = self.report()
        lines = task.read_text(self.directory / "source/source.txt").splitlines()
        other = next(line for line in reversed(lines) if len(line.strip()) > 4 and line != lines[0])
        report["samples"][0]["source_quote"] = other
        self.save_report(report)
        with self.assertRaises(task.Error):
            self.deliver()

    def test_out_of_bounds_string_bool_and_empty_locators(self):
        self.setup_ready()
        for locator in ({"kind": "line", "start": 1, "end": 999999}, "开头", {"kind": "line", "start": True, "end": 2}, {"kind": "line", "start": 0, "end": 2}):
            report = self.report()
            report["samples"][0]["source_locator"] = locator
            self.save_report(report)
            with self.assertRaises(task.Error):
                self.review()
            self.assertEqual(self.report()["result"], "pending")

    def test_forged_quote_rejected_even_if_elsewhere_in_source(self):
        self.setup_ready()
        report = self.report()
        report["samples"][0]["source_quote"] = "全文其他位置的真实文字也不能替代所选范围"
        self.save_report(report)
        with self.assertRaisesRegex(task.Error, "引文"):
            self.deliver()

    def test_pending_and_blocked_report_not_delivered(self):
        self.setup_ready()
        report = self.report()
        report["result"] = "pending"
        self.save_report(report)
        self.assertFalse(self.deliver()["ok"])
        report = self.report()
        report["result"] = "pass"
        report["risk_checks"][0]["blocking_issues"] = ["金额待核对"]
        self.save_report(report)
        self.assertFalse(self.deliver()["ok"])

    def test_parts_preserve_order_and_bytes_reject_duplicate_missing(self):
        self.setup_ready()
        manifest_path, manifest = task.load_task(str(self.directory))
        first, second = self.root / "p1.md", self.root / "p2.md"
        first.write_bytes("前段  \r\n".encode("utf-8"))
        second.write_bytes("\n后段\n".encode("utf-8"))
        task.join_parts(manifest_path, manifest, [str(first), str(second)])
        self.assertEqual((self.directory / "draft.md").read_bytes(), first.read_bytes() + b"\n\n" + second.read_bytes())
        task.join_parts(manifest_path, manifest, [str(first), str(second)])
        before = (self.directory / "draft.md").read_bytes()
        for bad in ([str(first), str(first)], [str(first), str(self.root / "missing.md")], [str(self.source)]):
            with self.assertRaises(task.Error):
                task.join_parts(manifest_path, manifest, bad)
            self.assertEqual(before, (self.directory / "draft.md").read_bytes())

    def test_output_aliases_and_hardlinks_rejected(self):
        self.setup_ready()
        for target in (self.source, self.directory / "draft.md", self.directory / "source/source.txt", self.directory / "review-report.json"):
            before = target.read_bytes()
            with self.assertRaises(task.Error):
                task.deliver(self.args("deliver", "--task", str(self.directory), "--output", str(target), "--replace"))
            self.assertEqual(target.read_bytes(), before)
        link = self.root / "source-hardlink.txt"
        os.link(self.source, link)
        with self.assertRaises(task.Error):
            task.deliver(self.args("deliver", "--task", str(self.directory), "--output", str(link), "--replace"))

    def test_replace_backs_up_and_requires_explicit_flag(self):
        self.setup_ready()
        output = self.root / "final.md"
        output.write_bytes(b"old content")
        with self.assertRaises(task.Error):
            self.deliver()
        self.assertEqual(output.read_bytes(), b"old content")
        result = self.deliver("--replace")
        self.assertTrue(result["ok"])
        self.assertEqual(Path(result["backup"]).read_bytes(), b"old content")

    def test_get_create_and_update_mock_existing_save_modules(self):
        self.setup_ready()
        module = SimpleNamespace(load_config=Mock(return_value={}), get_note_detail=Mock(),
            build_new_note_content=lambda draft, original, scene, fmt: "wrapper\n" + draft,
            create_note=Mock(return_value={"code": 0, "data": {"id": "999"}}))
        draft = task.read_text(self.directory / "draft.md")
        module.get_note_detail.side_effect = [{"title": "原笔记"}, {"title": "校正后的标题", "content": "wrapper\n" + draft}]
        with patch.object(task, "load_save_module", return_value=module):
            result = self.deliver("--destination", "get-new", "--source-note-id", "123", "--output-title", "校正后的标题")
        self.assertTrue(result["ok"])
        module.create_note.assert_called_once()
        response = Mock()
        response.json.return_value = {"code": 0}
        module = SimpleNamespace(load_config=Mock(return_value={}), build_update_payload=lambda note_id, title, content: {"note_id": note_id, "content": content},
            update_note=Mock(return_value=response), get_note_detail=Mock(return_value={"content": draft}))
        with patch.object(task, "load_save_module", return_value=module):
            result = self.deliver("--destination", "get-update", "--target-note-id", "999")
        self.assertTrue(result["ok"])
        module.update_note.assert_called_once()

    def test_get_update_source_and_missing_target_rejected(self):
        self.setup_ready()
        manifest = task.inputs.read_json(self.directory / "task.json")
        manifest["source_note_id"] = "123"
        task.save_json(self.directory / "task.json", manifest)
        with patch.object(task, "load_save_module") as module:
            for args in (("--destination", "get-update"), ("--destination", "get-update", "--target-note-id", "123"), ("--destination", "get-new")):
                with self.assertRaises(task.Error):
                    self.deliver(*args)
            module.assert_not_called()

    def test_get_readback_mismatch_is_not_success(self):
        self.setup_ready()
        module = SimpleNamespace(load_config=Mock(return_value={}),
            get_note_detail=Mock(side_effect=[{"title": "原笔记"}, {"title": "校正标题", "content": "返回的正文不符"}]),
            build_new_note_content=lambda draft, original, scene, fmt: draft,
            create_note=Mock(return_value={"code": 0, "data": {"id": "999"}}))
        with patch.object(task, "load_save_module", return_value=module):
            result = self.deliver("--destination", "get-new", "--source-note-id", "123", "--output-title", "校正标题")
        self.assertFalse(result["ok"])
        self.assertEqual(result["read_back"], "mismatch")
        self.assertEqual(task.inputs.read_json(self.directory / "delivery-result.json")["note_id"], "999")

    def test_get_readback_error_records_created_id(self):
        self.setup_ready()
        module = SimpleNamespace(load_config=Mock(return_value={}),
            get_note_detail=Mock(side_effect=[{"title": "原笔记"}, OSError("sensitive transport detail")]),
            build_new_note_content=lambda draft, original, scene, fmt: draft,
            create_note=Mock(return_value={"code": 0, "data": {"id": "999"}}))
        with patch.object(task, "load_save_module", return_value=module):
            result = self.deliver("--destination", "get-new", "--source-note-id", "123", "--output-title", "校正标题")
        self.assertFalse(result["ok"])
        self.assertEqual(result["note_id"], "999")
        self.assertNotIn("sensitive", json.dumps(result))

    def test_concurrent_change_after_last_binding_not_copied(self):
        self.setup_ready()
        original = task.binding
        count = 0
        def race(manifest, report):
            nonlocal count
            value = original(manifest, report)
            count += 1
            if count == 2:
                with Path(manifest["draft"]).open("a", encoding="utf-8") as handle:
                    handle.write("\n未复核的新内容")
            return value
        with patch.object(task, "binding", side_effect=race), self.assertRaises(task.Error):
            self.deliver()
        self.assertFalse((self.root / "final.md").exists())

    def test_concurrent_blocker_added_before_save_is_respected(self):
        self.setup_ready()
        original = task.require_final_validation
        def race(*args, **kwargs):
            value = original(*args, **kwargs)
            report = self.report()
            report["blocking_issues"] = ["另一位编辑刚发现问题"]
            self.save_report(report)
            return value
        with patch.object(task, "require_final_validation", side_effect=race), self.assertRaises(task.Error):
            self.deliver()
        self.assertFalse((self.root / "final.md").exists())

    def test_concurrent_draft_change_during_review_invalidates_report(self):
        self.setup_ready()
        original = task.analyze_task
        def race(manifest, final=False):
            value = original(manifest, final)
            with Path(manifest["draft"]).open("a", encoding="utf-8") as handle:
                handle.write("\n另一个任务的改动")
            return value
        with patch.object(task, "analyze_task", side_effect=race), self.assertRaises(task.Error):
            self.review()
        self.assertEqual(self.report()["result"], "pending")
        self.assertIsNone(task.inputs.read_json(self.directory / "task.json")["review_binding"])

    def test_not_present_does_not_need_invented_draft_evidence(self):
        self.setup_ready()
        report = self.report()
        for item in report["risk_checks"]:
            if item["kind"] == "technical_interlude":
                item["source_locators"] = []
                item["draft_locators"] = []
        self.save_report(report)
        self.review()
        self.finish_report()
        report = self.report()
        for item in report["risk_checks"]:
            if item["kind"] == "technical_interlude":
                item["status"] = "not_present"
                item["semantic_reason"] = "源文仅讨论销售服务和客户顾虑，没有技术插曲。"
        self.save_report(report)
        self.assertTrue(self.deliver()["ok"])

    def test_same_inputs_review_preserves_completed_status(self):
        self.setup_ready()
        result = self.review()
        self.assertFalse(result["prior_completion_invalidated"])
        self.assertEqual(self.report()["result"], "pass")

    def test_cli_reports_failure_without_secret_values(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            rc = task.main(["review", "--task", str(self.root / "missing")])
        self.assertEqual(rc, 2)
        self.assertFalse(json.loads(stream.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
