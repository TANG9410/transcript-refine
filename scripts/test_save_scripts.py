#!/usr/bin/env python3
"""Mock-only regression for save scripts; never calls Get笔记."""

import contextlib
import importlib.util
import io
import sys
import tempfile
from pathlib import Path


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
    finally:
        sys.argv = old


def main():
    scripts = Path(__file__).resolve().parent
    update = load(scripts / "update-note.py", "update_note_under_test")
    create = load(scripts / "refine-transcript.py", "refine_transcript_under_test")
    update.load_config = create.load_config = lambda: {}
    failures = []
    test_temp_root = scripts / ".test-tmp-root"
    test_temp_root.mkdir(exist_ok=True)
    tempfile.tempdir = str(test_temp_root)

    with tempfile.TemporaryDirectory() as folder:
        draft = Path(folder) / "draft.md"
        draft.write_text("正文\n", encoding="utf-8")

        if invoke(update, ["--note-id", "1", "--file", str(draft), "--dry-run", "--no-cleanup", "--json"]) != 0 or not draft.exists():
            failures.append("update dry-run changed input")

        update.update_note = lambda config, payload: Response(500)
        if invoke(update, ["--note-id", "1", "--file", str(draft), "--json"]) != 3 or not draft.exists():
            failures.append("update API failure changed input")

        update.update_note = lambda config, payload: Response(200, {"code": 0})
        update.get_note_detail = lambda config, note_id: {"title": "旧标题", "content": "正文\n"}
        if invoke(update, ["--note-id", "1", "--file", str(draft), "--json"]) != 0 or not draft.exists():
            failures.append("update success changed input")
        update.get_note_detail = lambda config, note_id: {"title": "旧标题", "content": "不同正文"}
        if invoke(update, ["--note-id", "1", "--file", str(draft), "--json"]) != 2 or not draft.exists():
            failures.append("update read-back failure changed input")

        create.get_note_detail = lambda config, note_id: {"title": "源笔记", "content": "source"} if str(note_id) == "9" else {"title": "[交流实录稿] 源笔记", "content": create.expected_content}
        create.create_note = lambda config, title, content, dry_run=False: {"code": 0, "data": {"id": "10"}}
        original_build = create.build_new_note_content

        def build_and_capture(content, title, scene, fmt):
            result = original_build(content, title, scene, fmt)
            create.expected_content = result
            return result

        create.build_new_note_content = build_and_capture
        if invoke(create, ["--note-id", "9", "--file", str(draft), "--format", "交流实录稿", "--json"]) != 0 or not draft.exists():
            failures.append("create success changed input")
        create.get_note_detail = lambda config, note_id: {"title": "源笔记", "content": "source"} if str(note_id) == "9" else {"title": "错误标题", "content": "不同正文"}
        if invoke(create, ["--note-id", "9", "--file", str(draft), "--format", "交流实录稿", "--json"]) != 2 or not draft.exists():
            failures.append("create read-back failure changed input")
        if invoke(create, ["--note-id", "9", "--file", str(draft), "--format", "交流实录稿", "--dry-run", "--no-cleanup", "--json"]) != 0 or not draft.exists():
            failures.append("create dry-run changed input")

    test_temp_root.rmdir()
    print("save-script mock regression: " + ("PASS" if not failures else "FAIL"))
    for item in failures:
        print("- " + item)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
