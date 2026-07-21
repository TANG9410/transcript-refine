#!/usr/bin/env python3
"""更新用户明确指定的 Get笔记；保存后 read-back，永不删除本地文件。"""

import argparse
import json
import os
import sys

import requests

from note_io_common import verify_readback


CONFIG_PATHS = [
    os.path.expanduser("~/.workbuddy/skills/getnote/config.json"),
    os.path.expanduser("~/.getnote/config.json"),
]
API_BASE = "https://openapi.biji.com/open/api/v1/resource"


def resolve_config_path(explicit_config=None):
    candidates = [explicit_config] if explicit_config else []
    candidates.extend(CONFIG_PATHS)
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def load_config(explicit_config=None):
    path = resolve_config_path(explicit_config)
    if not path:
        raise FileNotFoundError(
            "Getnote optional adapter config not found. Use local Markdown output, or provide --config <your-local-config.json>."
        )
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_file_content(filepath):
    with open(filepath, "r", encoding="utf-8") as handle:
        return handle.read()


def build_update_payload(note_id, title=None, content=None):
    payload = {"note_id": int(note_id)}
    if title is not None:
        payload["title"] = title
    if content is not None:
        payload["content"] = content
    return payload


def update_note(config, payload, dry_run=False):
    if dry_run:
        return None
    return requests.post(
        f"{API_BASE}/note/update",
        headers={
            "Authorization": config["api_key"],
            "X-Client-ID": config["client_id"],
            "Content-Type": "application/json",
        },
        json=payload,
    )


def get_note_detail(config, note_id):
    response = requests.get(
        f"{API_BASE}/note/detail",
        headers={
            "Authorization": config["api_key"],
            "X-Client-ID": config["client_id"],
        },
        params={"id": note_id},
    )
    response.raise_for_status()
    return response.json().get("data", {}).get("note", {})


def emit(report, json_mode):
    if json_mode:
        print(json.dumps(report, ensure_ascii=False))
        return
    if report.get("dry_run"):
        print("[DRY RUN] 未更新笔记；本地文件保持不变")
    else:
        state = "OK" if report["ok"] else "ERROR"
        print(f"[{state}] 笔记 {report['note_id']} 更新后 read-back: {report['read_back']}")
        print(f"  标题核对: {report.get('title_verified')}")
        print(f"  正文核对: {report.get('content_verified')}")
        print(f"  正文长度: {report.get('content_chars')} chars")
        print(f"  SHA256: {report.get('content_sha256')}")
        print("  本地文件保持不变")
    if report.get("compatibility_warning"):
        print(f"[兼容] {report['compatibility_warning']}")


def main():
    parser = argparse.ArgumentParser(description="更新Get笔记并执行read-back；不删除本地文件")
    parser.add_argument("--note-id", required=True, help="用户明确指定的既有精炼笔记ID")
    parser.add_argument("--config", default=None, help="可选：使用者自己的本机 Getnote 配置路径")
    parser.add_argument("--title", default=None, help="新标题（可选）")
    parser.add_argument("--file", default=None, help="从文件读取内容（UTF-8）")
    parser.add_argument("--content", default=None, help="直接传入短文本")
    parser.add_argument("--dry-run", action="store_true", help="试运行，不实际更新")
    parser.add_argument("--no-cleanup", action="store_true", help="兼容旧命令；文件始终保留")
    parser.add_argument("--json", action="store_true", help="输出JSON保存与read-back结果")
    args = parser.parse_args()

    if not args.title and not args.file and not args.content:
        parser.error("至少提供 --title、--file 或 --content 中的一个")
    if args.file and args.content:
        parser.error("--file 和 --content 不能同时使用")

    content = read_file_content(args.file) if args.file else args.content
    warning = "--no-cleanup 已无需使用；脚本不会删除任何本地文件" if args.no_cleanup else None
    config = load_config(args.config)
    payload = build_update_payload(args.note_id, title=args.title, content=content)

    if args.dry_run:
        emit(
            {
                "ok": True,
                "operation": "update",
                "dry_run": True,
                "note_id": str(args.note_id),
                "read_back": "skipped",
                "input_preserved": True,
                "compatibility_warning": warning,
            },
            args.json,
        )
        return

    response = update_note(config, payload)
    if response.status_code != 200:
        raise RuntimeError(f"Getnote update HTTP {response.status_code}")
    data = response.json()
    if not (data.get("code") == 0 or data.get("data")):
        raise RuntimeError("Getnote update returned an error payload")

    verification = verify_readback(
        get_note_detail(config, args.note_id),
        expected_title=args.title,
        expected_content=content,
    )
    report = {
        "ok": verification["read_back"] == "verified",
        "operation": "update",
        "dry_run": False,
        "note_id": str(args.note_id),
        "input_preserved": True,
        "compatibility_warning": warning,
        **verification,
    }
    emit(report, args.json)
    if not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
