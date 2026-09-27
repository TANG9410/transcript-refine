#!/usr/bin/env python3
"""Deterministic source capture and review-hash filling; never judges semantics."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from note_io_common import VALIDATION_REPORT_CONTRACT, text_sha256


API_ENDPOINT = "https://openapi.biji.com/open/api/v1/resource/note/detail"
CONFIG_PATHS = (
    Path.home() / ".workbuddy/skills/getnote/config.json",
    Path.home() / ".getnote/config.json",
)
AUDIO_TYPES = {"audio", "local_audio", "recorder_audio", "meeting"}
WEB_TYPES = {"link", "web_page"}
TIME_RE = re.compile(r"\[(\d{2}:\d{2}(?::\d{2})?)\]")


class InputError(Exception):
    """Messages contain local field names and positions, never remote payloads."""


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def read_json(path):
    try:
        return json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise InputError("invalid UTF-8 JSON; inspect the original file locally") from None


def note_id_string(value):
    # JSON integer literals remain exact in Python; floats/bools are not IDs.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise InputError("note ID must be a decimal string or an exact JSON integer")
    value = str(value)
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise InputError("note ID must contain decimal digits only")
    return value


def unwrap_response(payload):
    for _ in range(6):
        if not isinstance(payload, dict):
            raise InputError("response must be a JSON object")
        if payload.get("isError") or payload.get("success") is False or payload.get("error"):
            raise InputError("response reports an error; no fact source created")
        if "code" in payload and payload["code"] not in (0, "0", None):
            raise InputError("response reports a non-success code; no fact source created")
        if isinstance(payload.get("content"), list):
            texts = [x.get("text") for x in payload["content"]
                     if isinstance(x, dict) and x.get("type") == "text"]
            if len(texts) != 1 or not isinstance(texts[0], str):
                raise InputError("MCP wrapper must contain exactly one JSON text result")
            try:
                payload = json.loads(texts[0])
            except ValueError:
                raise InputError("MCP text result is not complete JSON") from None
        elif isinstance(payload.get("data"), dict):
            payload = payload["data"]
        elif isinstance(payload.get("note"), dict):
            payload = payload["note"]
        else:
            return payload
    raise InputError("unsupported response nesting")


def extract_source(payload, expected_id):
    note = unwrap_response(payload)
    if note_id_string(note.get("id")) != note_id_string(expected_id):
        raise InputError("response note ID does not match requested ID")
    kind = note.get("note_type")
    if "transcript" in note:
        if kind is not None and kind not in AUDIO_TYPES:
            raise InputError("transcript conflicts with note_type")
        content, field = note["transcript"], "transcript (get_note_transcript / audio.original)"
        kind = kind or "audio-transcript (subtype not returned)"
    elif kind in AUDIO_TYPES:
        content, field = (note.get("audio") or {}).get("original"), "audio.original"
    elif kind in WEB_TYPES:
        content, field = (note.get("web_page") or {}).get("content"), "web_page.content"
    elif kind == "plain_text":
        content, field = note.get("original", note.get("content")), "content (plain_text)"
    else:
        raise InputError("unsupported or missing note_type; locate the original field first")
    if not isinstance(content, str) or not content.strip():
        # Some get_note_original versions can fall back to a summary. Do not trust
        # an unlocated `original` for audio/web notes when the raw field is absent.
        raise InputError(f"missing non-empty {field}; obtain full detail, never substitute a summary")
    return content, {"note_id": note_id_string(expected_id), "note_type": kind,
                     "field": field, "updated_at": note.get("updated_at")}


def read_response(path, call_id=None):
    path = Path(path)
    if not call_id:
        raw = path.read_bytes()
        return raw, read_json(path), {"response_file": str(path.resolve())}
    # WorkBuddy stores short results inline in the session JSONL. Select the
    # exact recorded tool call, rather than asking the model to copy its text.
    calls, results = [], []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        item = json.loads(line)
        if item.get("callId") != call_id:
            continue
        if item.get("type") == "function_call":
            calls.append(item)
        elif item.get("type") == "function_call_result":
            results.append(item)
    if len(calls) != 1 or len(results) != 1:
        raise InputError("call ID must identify exactly one call and one recorded result")
    args = json.loads(calls[0].get("arguments", "{}"))
    tool = args.get("toolName") or calls[0].get("name")
    if tool not in {"mcp__getnote__get_note_transcript", "mcp__getnote__get_note_original",
                    "mcp__getnote__get_note"}:
        raise InputError("call ID is not a supported Getnote read call")
    output = results[0].get("output", {})
    if results[0].get("status") in {"error", "failed"}:
        raise InputError("recorded read call failed; no fact source created")
    if output.get("type") != "text" or not isinstance(output.get("text"), str):
        raise InputError("recorded result is not a JSON text response")
    raw = output["text"].encode("utf-8")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise InputError("recorded result is not complete JSON") from None
    params = args.get("params", args)
    requested_id = note_id_string(params.get("id", params.get("note_id")))
    return raw, payload, {"response_file": str(path.resolve()), "call_id": call_id,
                          "tool": tool, "requested_note_id": requested_id}


def fetch_response(note_id, config_path=None, no_proxy=False):
    paths = [Path(config_path)] if config_path else CONFIG_PATHS
    config = next((read_json(p) for p in paths if p.is_file()), None)
    if not isinstance(config, dict) or not all(
        isinstance(config.get(k), str) and config[k] for k in ("api_key", "client_id")
    ):
        raise InputError("Getnote read credentials are missing; credential values are never logged")
    request = Request(API_ENDPOINT + "?" + urlencode({"id": note_id}), method="GET",
                      headers={"Authorization": config["api_key"], "X-Client-ID": config["client_id"]})
    opener = build_opener(ProxyHandler({})) if no_proxy else build_opener()
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read()
    except HTTPError as exc:
        exc.close()
        raise InputError(f"read request returned HTTP {exc.code}; root cause not determined") from None
    except (URLError, TimeoutError, OSError):
        raise InputError("read request failed; root cause not determined (credentials/body omitted)") from None
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise InputError("read response is not complete UTF-8 JSON; root cause not determined") from None
    return raw, payload, {"method": "GET", "endpoint": API_ENDPOINT,
                          "query": {"id": note_id}, "proxy": "disabled-explicitly" if no_proxy else "environment"}


def inspect_times(text):
    previous = None
    markers, issues = [], []
    for line_no, line in enumerate(text.splitlines(), 1):
        for match in TIME_RE.finditer(line):
            stamp = match.group(1)
            parts = list(map(int, stamp.split(":")))
            seconds = sum(n * (60 ** i) for i, n in enumerate(reversed(parts)))
            if previous and seconds < previous[0]:
                issues.append({"line": line_no, "previous": previous[1], "current": stamp})
            previous = (seconds, stamp)
            markers.append(stamp)
    return {"timestamp_count": len(markers), "first_timestamp": markers[0] if markers else None,
            "last_timestamp": markers[-1] if markers else None, "backwards": issues,
            "interpretation": "positions only; original unchanged; no fault attribution"}


def ensure_exact(actual, expected):
    if actual == expected:
        return
    limit = min(len(actual), len(expected))
    offset = next((i for i in range(limit) if actual[i] != expected[i]), limit)
    raise InputError(f"source mismatch at byte {offset}; no text repaired or accepted")


def write_verified(path, data, replace=False):
    path = Path(path)
    if path.exists() and not replace:
        raise InputError("output already exists; use --verify-only or a fresh task directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pending-", delete=False) as tmp:
        pending = Path(tmp.name)
        tmp.write(data)
    # On failure retain the pending file as diagnostic evidence, not an approved output.
    ensure_exact(pending.read_bytes(), data)
    if replace:
        os.replace(pending, path)
    else:
        if path.exists():
            raise InputError("output appeared during write; existing output preserved")
        os.rename(pending, path)
    ensure_exact(path.read_bytes(), data)


def load_source_inputs(args):
    """Read and validate before creating any output; reused by the unified entrypoint."""
    note_id = note_id_string(args.note_id)
    if args.call_id and not args.response_file:
        raise InputError("--call-id requires --response-file")
    if args.response_file:
        raw, payload, origin = read_response(args.response_file, args.call_id)
    else:
        raw, payload, origin = fetch_response(note_id, args.config, args.no_proxy)
    if origin.get("requested_note_id", note_id) != note_id:
        raise InputError("recorded request ID does not match requested ID")
    content, meta = extract_source(payload, note_id)
    return raw, content, meta, origin


def source_command(args, captured=None):
    raw, content, meta, origin = captured if captured is not None else load_source_inputs(args)
    note_id = note_id_string(args.note_id)
    encoded = content.encode("utf-8")
    directory = Path(args.out_dir)
    source, raw_path, record = (directory / x for x in ("source.txt", "response.json", "source-record.json"))
    if args.verify_only:
        ensure_exact(source.read_bytes(), encoded)
        return {"ok": True, "operation": "source-verify", "chars": len(content),
                "source_sha256": text_sha256(content), "time_check": inspect_times(content)}
    if any(p.exists() for p in (source, raw_path, record)):
        raise InputError("source outputs already exist; verify them or use a fresh task directory")
    metadata = {"version": 1, "captured_at": datetime.now(timezone.utc).isoformat(), **meta,
                "origin": origin, "response_file": "response.json", "source_file": "source.txt",
                "response_sha256_bytes": sha_bytes(raw), "source_sha256_bytes": sha_bytes(encoded),
                "source_sha256": text_sha256(content), "chars": len(content),
                "time_check": inspect_times(content), "verification": "exact-utf8-bytes"}
    write_verified(raw_path, raw)
    write_verified(source, encoded)
    # Re-read the saved response and extract again before approving this source.
    saved, _ = extract_source(read_json(raw_path), note_id)
    ensure_exact(source.read_bytes(), saved.encode("utf-8"))
    write_verified(record, json_bytes(metadata))
    return {"ok": True, "operation": "source", "out_dir": str(directory.resolve()), **metadata}


def review_hashes_command(args):
    paths = {"source_sha256": Path(args.source), "draft_sha256": Path(args.draft)}
    if args.speaker_map:
        paths["speaker_map_sha256"] = Path(args.speaker_map)
    review_path = Path(args.review_report)
    output = Path(args.output) if args.output else review_path
    protected = [*paths.values(), Path(args.validation_report)]
    if any(output.resolve() == p.resolve() for p in protected):
        raise InputError("review output must not overwrite a source, draft, map or validation report")
    validation, review = read_json(args.validation_report), read_json(review_path)
    if not isinstance(validation, dict) or validation.get("validation_report_contract") != VALIDATION_REPORT_CONTRACT:
        raise InputError("input is not a complete validator report")
    if not all(k in validation for k in ("ok", "structural_ok", "stats", "mode", "issues")):
        raise InputError("validator report is cropped or incomplete")
    if not isinstance(review, dict) or review.get("version") != 1 or review.get("mode") != validation.get("mode"):
        raise InputError("review must use version 1 and the same mode as the validator report")
    hashes = {}
    for key, path in paths.items():
        value = validation.get(key)  # Intentionally never read these from stats.
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise InputError(f"missing or invalid TOP-LEVEL {key}; preserve the complete validator output")
        actual = text_sha256(path.read_bytes().decode("utf-8-sig"))
        if actual != value:
            raise InputError(f"{key} mismatch: input file changed or report is stale; regenerate structural report")
        hashes[key] = value
    if not args.speaker_map and validation.get("speaker_map_sha256") is not None:
        raise InputError("validator report used a speaker map; provide --speaker-map")
    hashes.setdefault("speaker_map_sha256", None)
    updated = dict(review)
    updated.update(hashes)
    # Only hashes change. In particular, pass/status/unresolved lists are untouched.
    write_verified(output, json_bytes(updated), replace=output.resolve() == review_path.resolve())
    if read_json(output) != updated:
        raise InputError("review report read-back mismatch")
    return {"ok": True, "operation": "review-hashes", "output": str(output.resolve()),
            "hashes": hashes, "semantic_status_unchanged": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    source = commands.add_parser("source", help="capture or verify the exact source, without rewriting")
    source.add_argument("--note-id", required=True)
    source.add_argument("--response-file", help="actual response JSON; or WorkBuddy JSONL with --call-id")
    source.add_argument("--call-id", help="exact Getnote tool call ID in the recorded WorkBuddy JSONL")
    source.add_argument("--out-dir", required=True)
    source.add_argument("--config", help="existing Getnote credential file (path only)")
    source.add_argument("--no-proxy", action="store_true", help="explicit per-call network choice; not a diagnosis")
    source.add_argument("--verify-only", action="store_true", help="compare existing source.txt; never overwrite it")
    review = commands.add_parser("review-hashes", help="verify top-level hashes and fill only review hash fields")
    for name in ("validation-report", "source", "draft", "review-report"):
        review.add_argument("--" + name, required=True)
    review.add_argument("--speaker-map")
    review.add_argument("--output", help="optional new review file; default updates only input review hashes")
    args = parser.parse_args(argv)
    try:
        result = source_command(args) if args.command == "source" else review_hashes_command(args)
    except InputError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, TypeError, AttributeError):
        # Do not echo arbitrary payload/config values from exception messages.
        print(json.dumps({"ok": False, "error": "local I/O or response format error; inspect input locally"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
