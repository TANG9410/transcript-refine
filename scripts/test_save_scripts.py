#!/usr/bin/env python3
"""Mock-only regression for save scripts; never calls Get笔记."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

from note_io_common import text_sha256
from validate_transcript_artifact import main as validate_main


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"code": 0, "data": {}}

    def json(self):
        return self._payload


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke(module, argv):
    old = sys.argv
    sys.argv = [str(module.__file__), *argv]
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            module.main()
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    except RuntimeError:
        return 3
    except (ValueError, OSError, json.JSONDecodeError):
        return 4
    finally:
        sys.argv = old


def main():
    scripts = Path(__file__).resolve().parent
    update = load(scripts / "update-note.py", "update_note_under_test")
    create = load(scripts / "refine-transcript.py", "refine_transcript_under_test")
    update.load_config = create.load_config = lambda: {}
    failures = []

    test_temp_root = scripts / ".test-tmp-root"
    test_temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="save-script-", dir=test_temp_root) as folder:
        draft = Path(folder) / "draft.md"
        source = scripts.parent / "evals" / "sample-exchange-fidelity-source.txt"
        review = scripts.parent / "evals" / "sample-exchange-fidelity-review-good.json"
        draft.write_text(
            (scripts.parent / "evals" / "sample-exchange-fidelity-good.md").read_text(encoding="utf-8-sig"),
            encoding="utf-8",
        )
        validation = Path(folder) / "validation.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            validation_code = validate_main(
                [
                    "--mode",
                    "exchange",
                    "--draft",
                    str(draft),
                    "--source",
                    str(source),
                    "--speaker-state",
                    "stable",
                    "--final-check",
                    "--review-report",
                    str(review),
                    "--report-out",
                    str(validation),
                ]
            )
        if validation_code != 0 or not validation.exists():
            failures.append("validator did not produce a complete save report")
        gate_args = ["--validation-report", str(validation)]
        if invoke(update, ["--note-id", "1", "--file", str(draft), *gate_args, "--dry-run", "--no-cleanup", "--json"]) != 0 or not draft.exists():
            failures.append("update dry-run changed input")

        if invoke(update, ["--note-id", "1", "--file", str(draft), "--dry-run", "--json"]) != 4:
            failures.append("update accepted content without validation report")
        invalid_validation = Path(folder) / "validation-invalid.json"
        invalid_validation.write_text(json.dumps({"final_ready": False, "draft_sha256": "0" * 64}), encoding="utf-8")
        if invoke(update, ["--note-id", "1", "--file", str(draft), "--validation-report", str(invalid_validation), "--dry-run", "--json"]) != 4:
            failures.append("update accepted invalid validation report")
        forged_validation = Path(folder) / "validation-forged-minimal.json"
        forged_validation.write_text(
            json.dumps(
                {
                    "final_ready": True,
                    "draft_sha256": text_sha256(draft.read_text(encoding="utf-8")),
                    "mode": "exchange",
                }
            ),
            encoding="utf-8",
        )
        if invoke(update, ["--note-id", "1", "--file", str(draft), "--validation-report", str(forged_validation), "--dry-run", "--json"]) != 4:
            failures.append("update accepted forged minimal final_ready report")
        forged_complete = Path(folder) / "validation-forged-complete-fields.json"
        forged_payload = json.loads(validation.read_text(encoding="utf-8"))
        forged_payload.pop("review_evidence", None)
        forged_payload.pop("review_evidence_sha256", None)
        forged_complete.write_text(json.dumps(forged_payload, ensure_ascii=False), encoding="utf-8")
        if invoke(update, ["--note-id", "1", "--file", str(draft), "--validation-report", str(forged_complete), "--dry-run", "--json"]) != 4:
            failures.append("update accepted complete fields without validated review evidence")
        forged_digest = Path(folder) / "validation-forged-evidence-digest.json"
        forged_payload = json.loads(validation.read_text(encoding="utf-8"))
        forged_payload["review_evidence_sha256"] = "0" * 64
        forged_digest.write_text(json.dumps(forged_payload, ensure_ascii=False), encoding="utf-8")
        if invoke(update, ["--note-id", "1", "--file", str(draft), "--validation-report", str(forged_digest), "--dry-run", "--json"]) != 4:
            failures.append("update accepted mismatched review evidence digest")

        update.update_note = lambda config, payload: Response(500)
        if invoke(update, ["--note-id", "1", "--file", str(draft), *gate_args, "--json"]) != 3 or not draft.exists():
            failures.append("update API failure changed input")

        update.update_note = lambda config, payload: Response(200, {"code": 0})
        update.get_note_detail = lambda config, note_id: {"title": "旧标题", "content": draft.read_text(encoding="utf-8")}
        if invoke(update, ["--note-id", "1", "--file", str(draft), *gate_args, "--json"]) != 0 or not draft.exists():
            failures.append("update success changed input")
        update.get_note_detail = lambda config, note_id: {"title": "旧标题", "content": "不同正文"}
        if invoke(update, ["--note-id", "1", "--file", str(draft), *gate_args, "--json"]) != 2 or not draft.exists():
            failures.append("update read-back failure changed input")

        create.get_note_detail = lambda config, note_id: {"title": "源笔记", "content": "source"} if str(note_id) == "9" else {"title": "[交流实录稿] 源笔记", "content": create.expected_content}
        create.create_note = lambda config, title, content, dry_run=False: {"code": 0, "data": {"id": "10"}}
        original_build = create.build_new_note_content

        def build_and_capture(content, title, scene, fmt):
            result = original_build(content, title, scene, fmt)
            create.expected_content = result
            return result

        create.build_new_note_content = build_and_capture
        if invoke(create, ["--note-id", "9", "--file", str(draft), "--format", "交流实录稿", *gate_args, "--json"]) != 0 or not draft.exists():
            failures.append("create success changed input")
        if invoke(create, ["--note-id", "9", "--file", str(draft), "--format", "交流实录稿", "--dry-run", "--json"]) != 4:
            failures.append("create accepted content without validation report")
        create.get_note_detail = lambda config, note_id: {"title": "源笔记", "content": "source"} if str(note_id) == "9" else {"title": "错误标题", "content": "不同正文"}
        if invoke(create, ["--note-id", "9", "--file", str(draft), "--format", "交流实录稿", *gate_args, "--json"]) != 2 or not draft.exists():
            failures.append("create read-back failure changed input")
        if invoke(create, ["--note-id", "9", "--file", str(draft), "--format", "交流实录稿", *gate_args, "--dry-run", "--no-cleanup", "--json"]) != 0 or not draft.exists():
            failures.append("create dry-run changed input")

    print("save-script mock regression: " + ("PASS" if not failures else "FAIL"))
    for item in failures:
        print("- " + item)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
