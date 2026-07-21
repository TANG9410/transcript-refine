#!/usr/bin/env python3
"""Regression for the optional Get笔记 adapter configuration boundary."""

import importlib.util
import json
import tempfile
from pathlib import Path


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    scripts = Path(__file__).resolve().parent
    modules = [
        load(scripts / "refine-transcript.py", "refine_optional_config"),
        load(scripts / "update-note.py", "update_optional_config"),
    ]
    failures = []
    test_temp_root = scripts / ".test-tmp-root"
    test_temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=test_temp_root) as folder:
        root = Path(folder)
        config = root / "getnote.json"
        config.write_text(json.dumps({"api_key": "test", "client_id": "test"}), encoding="utf-8")
        for module in modules:
            module.CONFIG_PATHS = [str(root / "missing.json")]
            try:
                module.load_config()
                failures.append(f"{module.__name__}: missing config unexpectedly succeeded")
            except FileNotFoundError as exc:
                if "optional adapter" not in str(exc):
                    failures.append(f"{module.__name__}: missing config message is unsafe")
            loaded = module.load_config(str(config))
            if loaded != {"api_key": "test", "client_id": "test"}:
                failures.append(f"{module.__name__}: explicit config did not load")
    test_temp_root.rmdir()
    print("optional-getnote regression: " + ("PASS" if not failures else "FAIL"))
    for failure in failures:
        print("- " + failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())