"""Offline input preparation regressions; fixtures contain no user transcripts."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import prepare_transcript_inputs as prep
from note_io_common import VALIDATION_REPORT_CONTRACT, text_sha256


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.note_id = "1000000000000000001"
        self.original = "[01:25:11] 说话人1\r\n原句。\r\n[01:25:51] 说话人2\n确认。\n"
        self.raw = self.root / "raw.json"
        self.put(self.raw, {"id": self.note_id, "transcript": self.original})
        self.out = self.root / "capture"

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, path, value):
        path.write_bytes(prep.json_bytes(value))

    def run_cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = prep.main(list(map(str, args)))
        return code, json.loads(output.getvalue())

    def source(self, *extra):
        return self.run_cli("source", "--note-id", self.note_id,
                            "--response-file", self.raw, "--out-dir", self.out, *extra)

    def test_direct_capture_preserves_unicode_and_newlines(self):
        code, result = self.source()
        self.assertEqual(code, 0)
        self.assertEqual((self.out / "source.txt").read_bytes(), self.original.encode())
        self.assertEqual((self.out / "response.json").read_bytes(), self.raw.read_bytes())
        self.assertEqual(result["time_check"]["timestamp_count"], 2)
        self.assertEqual(self.source("--verify-only")[0], 0)

    def test_tampered_timestamp_rejected(self):
        self.source()
        (self.out / "source.txt").write_bytes(self.original.replace("01:25:51", "00:25:51").encode())
        self.assertEqual(self.source("--verify-only")[0], 2)

    def test_added_ending_rejected(self):
        self.source()
        path = self.out / "source.txt"
        path.write_bytes(path.read_bytes() + "[02:06:00] 拜拜。".encode())
        self.assertEqual(self.source("--verify-only")[0], 2)

    def test_repeated_capture_does_not_overwrite(self):
        self.source()
        self.assertEqual(self.source()[0], 2)

    def test_anomaly_in_real_input_is_preserved_and_located(self):
        text = self.original.replace("01:25:51", "00:25:51")
        self.put(self.raw, {"id": self.note_id, "transcript": text})
        code, result = self.source()
        self.assertEqual(code, 0)
        self.assertEqual(result["time_check"]["backwards"][0]["line"], 3)
        self.assertEqual((self.out / "source.txt").read_bytes(), text.encode())

    def test_rejected_inputs_create_no_fact_source(self):
        bad = [
            {"id": "123", "transcript": self.original},
            {"id": float(self.note_id), "transcript": self.original},
            {"id": True, "transcript": self.original},
            {"id": self.note_id, "note_type": "meeting", "content": "summary"},
            {"id": self.note_id, "note_type": "meeting", "original": "possible summary fallback"},
            {"id": self.note_id, "transcript": " "},
            {"success": False, "data": {"id": self.note_id, "transcript": self.original}},
            {"code": 10500, "id": self.note_id, "transcript": self.original},
            {"isError": True, "content": []},
            {"id": self.note_id, "note_type": "plain_text", "transcript": self.original},
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                self.put(self.raw, payload)
                self.assertEqual(self.source()[0], 2)
                self.assertFalse(self.out.exists())

    def test_supported_note_fields_and_mcp_wrapper(self):
        notes = [
            {"id": self.note_id, "note_type": "meeting", "audio": {"original": self.original}},
            {"id": int(self.note_id), "note_type": "recorder_audio", "audio": {"original": self.original}},
            {"id": self.note_id, "note_type": "web_page", "web_page": {"content": self.original}},
            {"id": self.note_id, "note_type": "plain_text", "content": self.original},
        ]
        for note in notes:
            with self.subTest(kind=note["note_type"]):
                response = {"success": True, "data": {"note": note}}
                wrapper = {"content": [{"type": "text", "text": json.dumps(response)}]}
                self.assertEqual(prep.extract_source(wrapper, self.note_id)[0], self.original)

    def test_complete_workbuddy_record_and_requested_id(self):
        self.raw = self.root / "session.jsonl"
        rows = [
            {"type": "function_call", "callId": "c1", "name": "DeferExecuteTool",
             "arguments": json.dumps({"toolName": "mcp__getnote__get_note_transcript",
                                      "params": {"id": self.note_id}})},
            {"type": "function_call_result", "callId": "c1", "status": "success",
             "output": {"type": "text", "text": json.dumps({"id": self.note_id, "transcript": self.original})}},
        ]
        self.raw.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
        self.assertEqual(self.source("--call-id", "c1")[0], 0)
        self.assertEqual(self.source("--call-id", "missing")[0], 2)
        rows[0]["arguments"] = rows[0]["arguments"].replace(self.note_id, "123")
        self.raw.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
        self.assertEqual(self.source("--call-id", "c1", "--verify-only")[0], 2)

    def test_pending_write_mismatch_does_not_publish_source(self):
        with patch.object(prep, "ensure_exact", side_effect=prep.InputError("mismatch")):
            self.assertEqual(self.source()[0], 2)
        self.assertFalse((self.out / "source.txt").exists())
        self.assertFalse((self.out / "source-record.json").exists())

    def test_readback_corruption_rejected_without_approval_record(self):
        rename = prep.os.rename

        def corrupt_after_write(src, dst):
            rename(src, dst)
            if Path(dst).name == "source.txt":
                Path(dst).write_bytes(b"corrupt")

        with patch.object(prep.os, "rename", side_effect=corrupt_after_write):
            self.assertEqual(self.source()[0], 2)
        self.assertFalse((self.out / "source-record.json").exists())

    def test_errors_do_not_echo_credentials_or_payload(self):
        sentinel = "SECRET_DO_NOT_LOG"
        self.raw.write_text('{"error":"' + sentinel + '"}', encoding="utf-8")
        code, result = self.source()
        self.assertEqual(code, 2)
        self.assertNotIn(sentinel, json.dumps(result))
        self.raw.write_text("not json " + sentinel, encoding="utf-8")
        self.assertNotIn(sentinel, json.dumps(self.source()[1]))

    def test_fixed_read_only_endpoint_and_error_redaction(self):
        config = self.root / "config.json"
        self.put(config, {"api_key": "SECRET_KEY", "client_id": "SECRET_CLIENT"})
        with patch.object(prep, "build_opener") as builder:
            opener = builder.return_value
            opener.open.return_value.__enter__.return_value.read.return_value = self.raw.read_bytes()
            raw, _, origin = prep.fetch_response(self.note_id, config)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.full_url, prep.API_ENDPOINT + "?id=" + self.note_id)
            self.assertNotIn("SECRET", json.dumps(origin))
            self.assertEqual(raw, self.raw.read_bytes())
            for error in (HTTPError(request.full_url, 500, "SECRET_KEY", {}, None), URLError("SECRET_CLIENT")):
                opener.open.side_effect = error
                with self.assertRaises(prep.InputError) as caught:
                    prep.fetch_response(self.note_id, config)
                self.assertNotIn("SECRET", str(caught.exception))

    def hash_fixture(self):
        self.src, self.draft, self.mapping = [self.root / name for name in ("source.txt", "draft.md", "map.json")]
        for path, value in ((self.src, self.original), (self.draft, "成稿。\n"), (self.mapping, "{}\n")):
            path.write_bytes(value.encode())
        self.validation, self.review = self.root / "validation.json", self.root / "review.json"
        self.v = {"validation_report_contract": VALIDATION_REPORT_CONTRACT, "ok": True,
                  "structural_ok": True, "final_ready": False, "mode": "exchange", "issues": [], "stats": {},
                  "source_sha256": text_sha256(self.original), "draft_sha256": text_sha256("成稿。\n"),
                  "speaker_map_sha256": text_sha256("{}\n")}
        self.r = {"version": 1, "mode": "exchange", "samples": [{"status": "fail"}],
                  "unresolved_items": ["still needs review"], "status": "pending", "final_ready": False}
        self.put(self.validation, self.v)
        self.put(self.review, self.r)

    def hashes(self, *extra):
        return self.run_cli("review-hashes", "--validation-report", self.validation,
                            "--source", self.src, "--draft", self.draft, "--speaker-map", self.mapping,
                            "--review-report", self.review, *extra)

    def test_top_level_hashes_without_final_check_preserve_semantic_status(self):
        self.hash_fixture()
        self.assertEqual(self.hashes()[0], 0)
        updated = prep.read_json(self.review)
        hashes = {k: updated.pop(k) for k in ("source_sha256", "draft_sha256", "speaker_map_sha256")}
        self.assertEqual(updated, self.r)
        self.assertEqual(hashes, {k: self.v[k] for k in hashes})

    def test_missing_top_level_cannot_be_rescued_by_stats(self):
        self.hash_fixture()
        before = self.review.read_bytes()
        self.v["stats"]["source_sha256"] = self.v.pop("source_sha256")
        self.put(self.validation, self.v)
        self.assertEqual(self.hashes()[0], 2)
        self.assertEqual(self.review.read_bytes(), before)

    def test_source_draft_and_map_changes_all_reject_without_writing(self):
        for which in ("src", "draft", "mapping"):
            with self.subTest(which=which):
                self.hash_fixture()
                before = self.review.read_bytes()
                path = getattr(self, which)
                path.write_bytes(path.read_bytes() + b"changed")
                self.assertEqual(self.hashes()[0], 2)
                self.assertEqual(self.review.read_bytes(), before)

    def test_cropped_report_rejected(self):
        self.hash_fixture()
        self.v.pop("structural_ok")
        self.put(self.validation, self.v)
        self.assertEqual(self.hashes()[0], 2)

    def test_hash_output_cannot_overwrite_inputs_or_other_existing_files(self):
        self.hash_fixture()
        for path in (self.src, self.draft, self.mapping, self.validation, self.raw):
            with self.subTest(path=path.name):
                before = path.read_bytes()
                self.assertEqual(self.hashes("--output", path)[0], 2)
                self.assertEqual(path.read_bytes(), before)

    def test_optional_map_omitted_only_when_validator_did_not_use_one(self):
        self.hash_fixture()
        command = ("review-hashes", "--validation-report", self.validation, "--source", self.src,
                   "--draft", self.draft, "--review-report", self.review)
        self.assertEqual(self.run_cli(*command)[0], 2)
        self.v["speaker_map_sha256"] = None
        self.put(self.validation, self.v)
        self.assertEqual(self.run_cli(*command)[0], 0)
        self.assertIsNone(prep.read_json(self.review)["speaker_map_sha256"])


if __name__ == "__main__":
    unittest.main()
