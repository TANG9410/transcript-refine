#!/usr/bin/env python3
"""创建新的精炼笔记；主稿类型由Skill传入，保存后read-back，永不删除本地文件。"""

import argparse
import json
import os
import sys
from datetime import datetime

import requests

from note_io_common import verify_readback


CONFIG_PATHS = [
    os.path.expanduser("~/.workbuddy/skills/getnote/config.json"),
    os.path.expanduser("~/.getnote/config.json"),
]
API_BASE = "https://openapi.biji.com/open/api/v1/resource"
FORMATS = ("交流实录稿", "复盘纪要稿", "学习整理稿")


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


def build_new_note_content(refined_content, original_title, scene, fmt):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    refine_type = f"{scene} · {fmt}" if scene else fmt
    return "\n".join(
        [
            "### 🔄 逐字稿精炼版",
            "",
            f"> 精炼时间：{timestamp} | 精炼类型：{refine_type} | 源笔记：{original_title}",
            "",
            "---",
            refined_content.strip(),
        ]
    )


def create_note(config, title, content, dry_run=False):
    if dry_run:
        return {"success": True, "data": {"id": "(dry-run)"}}
    response = requests.post(
        f"{API_BASE}/note/save",
        headers={
            "Authorization": config["api_key"],
            "X-Client-ID": config["client_id"],
            "Content-Type": "application/json",
        },
        json={"note_type": "plain_text", "title": title, "content": content},
    )
    response.raise_for_status()
    return response.json()


def emit(report, json_mode):
    if json_mode:
        print(json.dumps(report, ensure_ascii=False))
        return
    if report.get("dry_run"):
        print("[DRY RUN] 未创建笔记；本地文件保持不变")
    else:
        state = "OK" if report["ok"] else "ERROR"
        print(f"[{state}] 新笔记 {report['note_id']} 创建后 read-back: {report['read_back']}")
        print(f"  标题核对: {report.get('title_verified')}")
        print(f"  正文核对: {report.get('content_verified')}")
        print(f"  正文长度: {report.get('content_chars')} chars")
        print(f"  SHA256: {report.get('content_sha256')}")
        print("  源笔记和本地文件保持不变")
    if report.get("compatibility_warning"):
        print(f"[兼容] {report['compatibility_warning']}")


def main():
    parser = argparse.ArgumentParser(description="创建新的精炼笔记并read-back；不删除本地文件")
    parser.add_argument("--note-id", required=True, help="源笔记ID（原笔记不动）")
    parser.add_argument("--config", default=None, help="可选：使用者自己的本机 Getnote 配置路径")
    parser.add_argument("--file", default=None, help="已完成校验的最终稿文件（UTF-8）")
    parser.add_argument("--scene", default=None, help="兼容元数据，不参与主稿路由")
    parser.add_argument("--format", choices=FORMATS, default=None, help="Skill已确定的主稿类型")
    parser.add_argument("--output-title", default=None, help="新建笔记标题")
    parser.add_argument("--fetch-only", action="store_true", help="兼容入口：只读取源笔记")
    parser.add_argument("--dry-run", action="store_true", help="试运行，不实际创建")
    parser.add_argument("--no-cleanup", action="store_true", help="兼容旧命令；文件始终保留")
    parser.add_argument("--json", action="store_true", help="输出JSON保存与read-back结果")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.fetch_only:
        note = get_note_detail(config, args.note_id)
        print(f"Title: {note.get('title')}")
        print(f"Note Type: {note.get('note_type')}")
        audio = note.get("audio", {})
        if audio and audio.get("original"):
            print("\n--- audio.original ---")
            print(audio["original"])
        else:
            print("\n--- content ---")
            print(note.get("content", ""))
        return

    if not args.file:
        parser.error("创建模式需要 --file")
    if not args.format:
        parser.error("创建模式需要由Skill明确传入 --format")

    source_note = get_note_detail(config, args.note_id)
    original_title = source_note.get("title", "未知笔记")
    refined_content = read_file_content(args.file)
    new_title = args.output_title or f"[{args.format}] {original_title}"
    new_content = build_new_note_content(refined_content, original_title, args.scene, args.format)
    warning = "--no-cleanup 已无需使用；脚本不会删除任何本地文件" if args.no_cleanup else None

    result = create_note(config, new_title, new_content, dry_run=args.dry_run)
    if args.dry_run:
        emit(
            {
                "ok": True,
                "operation": "create",
                "dry_run": True,
                "note_id": "(dry-run)",
                "read_back": "skipped",
                "input_preserved": True,
                "compatibility_warning": warning,
            },
            args.json,
        )
        return

    if not (result.get("success") or result.get("code") == 0 or result.get("data")):
        raise RuntimeError("Getnote create returned an error payload")
    note_id = result.get("data", {}).get("id") or result.get("data", {}).get("note_id")
    if not note_id:
        raise RuntimeError("Getnote create response missing note_id")

    verification = verify_readback(
        get_note_detail(config, note_id),
        expected_title=new_title,
        expected_content=new_content,
    )
    report = {
        "ok": verification["read_back"] == "verified",
        "operation": "create",
        "dry_run": False,
        "note_id": str(note_id),
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
