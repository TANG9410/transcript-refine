#!/usr/bin/env python3
"""Backward-compatible exchange transcript validator."""

import sys

from validate_transcript_artifact import main


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:], forced_mode="exchange"))
