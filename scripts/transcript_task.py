#!/usr/bin/env python3
"""逐字稿统一入口：准备材料 → 模型整理和复核 → 检查与交付。"""

import argparse
import copy
import difflib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import prepare_transcript_inputs as inputs
import validate_transcript_artifact as validator
import review_workspace as reviews
from note_io_common import json_sha256, require_final_validation, text_sha256, verify_readback


FORMATS = {"exchange": "交流实录稿", "review": "复盘纪要稿", "learning": "学习整理稿"}
RISK_KINDS = ("numbers_prices", "questions_objections", "commitments_next_steps",
              "speaker_asr_uncertainty", "technical_interlude")
Error = inputs.InputError


def read_text(path):
    return Path(path).read_bytes().decode("utf-8-sig")


def save_json(path, value, replace=True):
    inputs.write_verified(path, inputs.json_bytes(value), replace=replace)


def same_file(left, right):
    left, right = Path(left), Path(right)
    return left.resolve() == right.resolve() or (left.exists() and right.exists() and left.samefile(right))


def reject_alias(path, protected):
    if any(same_file(path, other) for other in protected if other):
        raise Error("输出路径与原文或任务输入重合（含联接/硬链接）；输入保持不变")


def template(mode):
    base = {"preserved_items": [], "unresolved_items": [], "blocking_issues": [], "status": "pending"}
    return {
        "version": 1, "mode": mode, "source_sha256": None, "draft_sha256": None,
        "speaker_map_sha256": None, "result": "pending", "unresolved_items": [], "blocking_issues": [],
        "samples": [{**copy.deepcopy(base), "kind": kind, "source_locator": None,
                     "draft_locator": None, "source_quote": "", "draft_quote": ""}
                    for kind in ("opening", "middle", "ending")],
        "risk_checks": [{**copy.deepcopy(base), "kind": kind, "source_locators": [],
                         "draft_locators": [], "source_quotes": [], "draft_quotes": [], "semantic_reason": ""}
                        for kind in RISK_KINDS],
    }


def task_path(value):
    path = Path(value).resolve()
    return path if path.name == "task.json" else path / "task.json"


def load_task(value):
    path = task_path(value)
    task = inputs.read_json(path)
    if task.get("version") != 1 or task.get("mode") not in FORMATS:
        raise Error("不支持的 task.json；请使用 prepare 创建任务")
    if task.get("skill_version") in {"4.1.1", "4.1.2"} and not task.get("review_input"):
        raise Error("新任务缺少 review_input，不能退回旧报告填写路径")
    source = Path(task["source"])
    if inputs.sha_bytes(source.read_bytes()) != task.get("source_sha256_bytes"):
        raise Error("事实源已变化；不能更新哈希接受变化。请核对原始返回并重新 prepare")
    return path, task


def report_items(report):
    for group in ("samples", "risk_checks"):
        items = report.get(group)
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
            raise Error(f"复核报告 {group} 必须为对象列表")
        for item in items:
            yield group, item


def selections(report):
    return [{"group": group, "kind": item.get("kind"),
             "source": item.get("source_locator") if group == "samples" else item.get("source_locators"),
             "draft": item.get("draft_locator") if group == "samples" else item.get("draft_locators")}
            for group, item in report_items(report)]


def binding(task, report):
    return {"source": inputs.sha_bytes(Path(task["source"]).read_bytes()),
            "draft": inputs.sha_bytes(Path(task["draft"]).read_bytes()),
            "speaker_map": inputs.sha_bytes(Path(task["speaker_map"]).read_bytes()) if task.get("speaker_map") else None,
            "selectors": json_sha256(selections(report)),
            "quotes": json_sha256([{key: item.get(key) for key in ("source_quote", "draft_quote", "source_quotes", "draft_quotes")}
                                   for _, item in report_items(report)]),
            "settings": json_sha256({k: task.get(k) for k in ("mode", "speaker_state", "people_count", "asr_channel_count", "expected_duration_seconds", "exchange_metadata_required")})}


def invalidate(report):
    report["result"] = "pending"
    for _, item in report_items(report):
        item["status"] = "pending"
    # Keep reasons, preserved_items and all outstanding issues for the editor.


def extract(text, locator):
    try:
        return validator.resolve_line_locator(text, locator)
    except ValueError as exc:
        raise Error(str(exc)) from None


def materialize(report, source, draft, verify=False):
    """Every supplied selector is strict. Unselected pending risks stay empty."""
    for group, item in report_items(report):
        for side, text in (("source", source), ("draft", draft)):
            if group == "samples":
                quote = extract(text, item.get(side + "_locator"))
                key = side + "_quote"
            else:
                locators = item.get(side + "_locators", [])
                if not isinstance(locators, list):
                    raise Error(f"{item.get('kind')} {side}_locators 必须为列表")
                quote = [extract(text, locator) for locator in locators]
                key = side + "_quotes"
            if verify:
                if item.get(key) != quote:
                    raise Error("复核引文与选定行范围不一致；重新 review 并核对，不接受手改引文")
            else:
                item[key] = quote


def analyze_task(task, final=False):
    draft, source = read_text(task["draft"]), read_text(task["source"])
    map_text = read_text(task["speaker_map"]) if task.get("speaker_map") else None
    review_text = read_text(task["review_report"]) if final else None
    report = validator.analyze(
        task["mode"], draft, source, task.get("expected_duration_seconds"),
        json.loads(map_text) if map_text else None, task.get("people_count"), task.get("asr_channel_count"),
        speaker_state=task["speaker_state"], final_check=final,
        review_report=json.loads(review_text) if review_text else None,
        speaker_map_sha256=text_sha256(map_text) if map_text else None,
        require_exchange_metadata=(task.get("mode") == "exchange" and task.get("exchange_metadata_required", False)),
    )
    report.update(source_sha256=text_sha256(source), draft_sha256=text_sha256(draft))
    if map_text:
        report["speaker_map_sha256"] = text_sha256(map_text)
    if review_text:
        report["review_report_sha256"] = text_sha256(review_text)
    return report


def prepare(args):
    directory = Path(args.task_dir).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise Error("任务目录必须为空，避免覆盖先前原文、成稿或复核")
    if args.source_file and (args.response_file or args.call_id):
        raise Error("本地原文不能同时指定 Get 返回文件或 call-id")
    if args.call_id and not args.response_file:
        raise Error("--call-id 需要 --response-file")
    source_dir = directory / "source"
    original_inputs = []
    if args.source_file:
        original = Path(args.source_file).resolve()
        data = original.read_bytes()
        text = data.decode("utf-8-sig")
        if not text.strip():
            raise Error("本地事实源不能为空")
        reject_alias(source_dir / "source.txt", [original])
        inputs.write_verified(source_dir / "source.txt", data)
        inputs.ensure_exact((source_dir / "source.txt").read_bytes(), original.read_bytes())
        save_json(source_dir / "source-record.json", {"version": 1, "origin": {"source_file": str(original)},
                  "verification": "exact-input-bytes", "source_sha256_bytes": inputs.sha_bytes(data),
                  "source_sha256": text_sha256(text), "chars": len(text), "time_check": inputs.inspect_times(text)})
        original_inputs.append(str(original))
    else:
        source_args = argparse.Namespace(note_id=args.note_id, response_file=args.response_file,
            call_id=args.call_id, out_dir=str(source_dir), config=args.config,
            no_proxy=args.no_proxy, verify_only=False)
        captured = inputs.load_source_inputs(source_args)
        inputs.source_command(source_args, captured=captured)
        if args.response_file:
            original_inputs.append(str(Path(args.response_file).resolve()))
        text = read_text(source_dir / "source.txt")
    task = {"version": 1, "skill_version": "4.1.2", "mode": args.mode,
            "source": str(source_dir / "source.txt"), "draft": str(directory / "draft.md"),
            "review_report": str(directory / "review-report.json"), "speaker_map": None,
            "review_input": str(directory / "review-input.json"), "review_revision": 0,
            "speaker_state": args.speaker_state, "people_count": args.people_count,
            "asr_channel_count": args.asr_channel_count, "expected_duration_seconds": args.expected_duration_seconds,
            "source_note_id": inputs.note_id_string(args.note_id) if args.note_id else None,
            "exchange_metadata_required": args.mode == "exchange",
            "get_config_path": str(Path(args.config).resolve()) if args.config else None,
            "original_inputs": original_inputs, "parts": [], "destination": "local",
            "source_sha256_bytes": inputs.sha_bytes((source_dir / "source.txt").read_bytes()),
            "long_source_choice": args.long_source_choice, "source_chars": len(text),
            "source_line_count": len(text.splitlines()), "review_binding": None}
    save_json(directory / "review-report.json", template(args.mode), replace=False)
    save_json(task["review_input"], reviews.input_template(template(args.mode)), replace=False)
    task["generated_report_sha256"] = inputs.sha_bytes(Path(task["review_report"]).read_bytes())
    save_json(directory / "task.json", task, replace=False)
    if len(text) > 35000 and args.long_source_choice != "keep-one":
        return {"ok": False, "operation": "prepare", "task": str(directory), "source_chars": len(text),
                "next": "原文已保存。超过35000字符：等待用户选择。明确保留一篇后 review --long-source-choice keep-one；确认拆篇则分别准备已确认范围的任务，不能在本任务直接单篇交付。"}
    return {"ok": True, "operation": "prepare", "task": str(directory), "source_chars": len(text),
            "source_line_count": len(text.splitlines()), "draft": task["draft"],
            "review_input": task["review_input"],
            "next": "阅读原文并写 draft.md；只在 review-input.json 选择样本与风险项行范围，再运行 review --task <目录>。正式报告由工具生成。查看行号：review --task <目录> --show-lines source --start 1 --end 40。"}


def check_long_source(task):
    if task["source_chars"] > 35000 and task.get("long_source_choice") != "keep-one":
        raise Error("长稿单篇交付尚未获明确选择；用户确认后传 --long-source-choice keep-one。若确认拆篇，请分别准备已确认范围任务")


def protected_paths(path, task):
    return [task["source"], task["draft"], task["review_report"], task.get("review_input"), task.get("speaker_map"), task.get("get_config_path"), path,
            *task.get("original_inputs", []), *task.get("parts", []),
            *[path.parent / name for name in ("structure.json", "validation-report.json", "review.md", "delivery-result.json")],
            *list((path.parent / "source").glob("*"))]


def join_parts(path, task, parts):
    files = [Path(p).resolve() for p in parts]
    if not files:
        raise Error("分稿列表不能为空")
    for index, file in enumerate(files):
        if not file.is_file():
            raise Error(f"分稿文件不存在：{file}")
        reject_alias(file, files[:index])
        protected = dict(task, parts=[])
        reject_alias(file, protected_paths(path, protected))
    chunks = [file.read_bytes() for file in files]
    for chunk in chunks:
        chunk.decode("utf-8")
    # Delimit blocks without stripping even one input character.
    joined = b"\n\n".join(chunks)
    reject_alias(task["draft"], [task["source"], task["review_report"], task.get("review_input"), task.get("speaker_map"),
                              *task.get("original_inputs", []), *files])
    inputs.write_verified(task["draft"], joined, replace=True)
    inputs.ensure_exact(Path(task["draft"]).read_bytes(), joined)
    task["parts"] = [str(file) for file in files]


def numbered(text, locator, context=2):
    lines = text.splitlines()
    start, end = max(1, locator["start"] - context), min(len(lines), locator["end"] + context)
    return "\n".join(f"{'>' if locator['start'] <= i <= locator['end'] else ' '} {i}: {lines[i-1]}"
                     for i in range(start, end + 1))


def review_markdown(report, source, draft, structure, binding_value):
    lines = ["# 原文与成稿复核", "", "程序只提取和对照文本；请读完整上下文后填写语义判断。",
             "", "绑定指纹：" + json_sha256(binding_value), "", "## 结构检查", "",
             "通过" if structure["ok"] else "\n".join("- " + value for value in structure["issues"])]
    for group, item in report_items(report):
        lines += ["", "## " + item["kind"], ""]
        source_locators = [item["source_locator"]] if group == "samples" else item["source_locators"]
        draft_locators = [item["draft_locator"]] if group == "samples" else item["draft_locators"]
        if not source_locators and not draft_locators:
            lines += ["未选位置：请先核对原文，再决定 checked 或 not_present。"]
        for title, text, locators in (("原文", source, source_locators), ("成稿", draft, draft_locators)):
            for locator in locators:
                lines += [f"### {title} {locator['start']}–{locator['end']} 行", "", "~~~~text", numbered(text, locator), "~~~~", ""]
        if source_locators and draft_locators:
            before = "\n".join(extract(source, x) for x in source_locators)
            after = "\n".join(extract(draft, x) for x in draft_locators)
            diff = "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile="原文", tofile="成稿", lineterm=""))
            lines += ["### 文字差异（不自动判定合理性）", "", "~~~~diff", diff or "选定文字一致", "~~~~"]
    return "\n".join(lines) + "\n"


def read_form(path, task):
    file = Path(task["review_input"])
    reject_alias(file, [p for p in protected_paths(path, task) if p and Path(p) != file])
    raw = file.read_bytes()
    form = json.loads(raw.decode("utf-8-sig"))
    reviews.validate_form(form, task["mode"], template(task["mode"]))
    return form, inputs.sha_bytes(raw)


def owned_report(task):
    raw = Path(task["review_report"]).read_bytes()
    if inputs.sha_bytes(raw) != task.get("generated_report_sha256"):
        raise Error("程序生成的 review-report.json 已被改写；不接受直接改通过状态或引文。保留现场并恢复已核实副本，正常只编辑 review-input.json")
    return json.loads(raw.decode("utf-8-sig"))


def save_owned_report(path, task, report):
    owned_report(task)  # Refuse an external edit made after the initial read.
    save_json(task["review_report"], report)
    task["generated_report_sha256"] = inputs.sha_bytes(Path(task["review_report"]).read_bytes())
    save_json(path, task)


def review_new(path, task):
    previous = owned_report(task)
    form, form_hash = read_form(path, task)
    report = reviews.assemble(form, previous, template(task["mode"]))
    source, draft = read_text(task["source"]), read_text(task["draft"])
    materialize(report, source, draft)
    current = binding(task, report)
    changed = task.get("review_binding") != current
    if changed:
        invalidate(report)
    structure = analyze_task(task)
    if binding(task, report) != current or read_form(path, task)[1] != form_hash:
        invalidate(report)
        task["review_binding"] = None
        save_owned_report(path, task, report)
        raise Error("生成复核材料时输入已变化；保留问题并重新 review")
    if changed:
        task["review_revision"] += 1
    task["review_binding"] = current
    save_owned_report(path, task, report)
    save_json(path.parent / "structure.json", structure)
    sections = reviews.compact_sections(report, source, draft, structure, task["review_revision"])
    inputs.write_verified(path.parent / "review.md", ("\n\n".join(sections) + "\n").encode("utf-8"), replace=True)
    return {"ok": structure["ok"], "operation": "review", "review": str(path.parent / "review.md"),
            "prior_completion_invalidated": changed, "issues": structure["issues"], "warnings": structure["warnings"],
            **reviews.page_result(sections, 1, task["review_revision"]),
            "next": "阅读本次显示的全部对照页（续页 review --task <目录> --page N）。核对正文和附录后，仅在 review-input.json 填对应 review_revision、逐项判断和处置，再 deliver；不编辑程序报告。"}


def review_view(args, path, task):
    if any((args.parts, args.speaker_map, args.speaker_state, args.people_count is not None,
            args.asr_channel_count is not None, args.long_source_choice, args.show_lines)):
        raise Error("--page/--full-diff 是只读查看，不能同时改变任务参数")
    report = owned_report(task) if task.get("review_input") else inputs.read_json(task["review_report"])
    if task.get("review_input"):
        form, _ = read_form(path, task)
        if selections(form) != selections(report):
            raise Error("位置已变化；先重新 review 再查看")
    if not task.get("review_binding") or binding(task, report) != task["review_binding"]:
        raise Error("复核材料已变化或尚未生成；先重新 review")
    source, draft = read_text(task["source"]), read_text(task["draft"])
    materialize(report, source, draft, verify=True)
    structure = analyze_task(task)
    sections = (reviews.full_diff_sections(report, source, draft) if args.full_diff else
                reviews.compact_sections(report, source, draft, structure, task.get("review_revision", "legacy")))
    return {"ok": True, "operation": "review-view", "read_only": True,
            **reviews.page_result(sections, args.page or 1, task.get("review_revision", "legacy"))}


def review(args):
    path, task = load_task(args.task)
    if args.page is not None or args.full_diff:
        return review_view(args, path, task)
    if args.show_lines:
        text = read_text(task[args.show_lines])
        end = args.end if args.end is not None else min(args.start + 39, len(text.splitlines()))
        locator = {"kind": "line", "start": args.start, "end": end}
        extract(text, locator)
        return {"ok": True, "operation": "show-lines", "file": task[args.show_lines],
                "line_count": len(text.splitlines()), "text": numbered(text, locator, context=0)}
    if args.long_source_choice:
        task["long_source_choice"] = args.long_source_choice
    check_long_source(task)
    for key in ("speaker_state", "people_count", "asr_channel_count"):
        if getattr(args, key) is not None:
            task[key] = getattr(args, key)
    if args.speaker_map:
        task["speaker_map"] = str(Path(args.speaker_map).resolve())
    if task.get("speaker_map"):
        reject_alias(task["speaker_map"], protected_paths(path, dict(task, speaker_map=None)))
    if args.parts:
        join_parts(path, task, args.parts)
    if task.get("review_input"):
        return review_new(path, task)
    report = inputs.read_json(task["review_report"])
    current = binding(task, report)
    changed = task.get("review_binding") != current
    if changed:
        invalidate(report)
        save_json(task["review_report"], report)
        task["review_binding"] = None
        save_json(path, task)
    source, draft = read_text(task["source"]), read_text(task["draft"])
    materialize(report, source, draft)
    save_json(task["review_report"], report)
    structure = analyze_task(task)
    refreshed = binding(task, report)
    if any(current[key] != refreshed[key] for key in current if key != "quotes"):
        invalidate(report)
        save_json(task["review_report"], report)
        task["review_binding"] = None
        save_json(path, task)
        raise Error("生成复核材料时输入被其他操作改动；旧状态已失效，请重新 review")
    current = refreshed
    save_json(path.parent / "structure.json", structure)
    task["review_binding"] = current
    save_json(path, task)
    inputs.write_verified(path.parent / "review.md", review_markdown(report, source, draft, structure, current).encode("utf-8"), replace=True)
    return {"ok": structure["ok"], "operation": "review", "review": str(path.parent / "review.md"),
            "prior_completion_invalidated": changed, "issues": structure["issues"], "warnings": structure["warnings"],
            "next": "阅读 review.md 的原文、成稿和差异；在 review-report.json 填写 preserved_items、status 和 result。保留未解决问题；改稿或改位置后重新 review。核对完成后 deliver --task <目录> --output <最终文件>。"}


def load_save_module(filename):
    spec = importlib.util.spec_from_file_location("transcript_save_" + filename.replace("-", "_"), Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save_get(args, task, draft, validation_path):
    require_final_validation(str(validation_path), draft, expected_mode=task["mode"])
    if args.destination == "get-new":
        source_id = args.source_note_id or task.get("source_note_id")
        if not source_id or not args.output_title:
            raise Error("新建 Get 需要源笔记 ID 和 --output-title 校正标题")
        source_id = inputs.note_id_string(source_id)
        if task.get("source_note_id") and source_id != task["source_note_id"]:
            raise Error("显式源笔记 ID 与 prepare 的事实源不同；不能改写来源关系")
        module = load_save_module("refine-transcript.py")
        config = inputs.read_json(task["get_config_path"]) if task.get("get_config_path") else module.load_config()
        original = module.get_note_detail(config, source_id)
        content = module.build_new_note_content(draft, original.get("title", "未知笔记"), None, FORMATS[task["mode"]])
        result = module.create_note(config, args.output_title, content)
        if not isinstance(result, dict) or not (result.get("success") or result.get("code") == 0 or result.get("data")):
            raise Error("Get 新建返回错误；请核对实际返回，不能归因为接口故障")
        note_id = result.get("data", {}).get("id") or result.get("data", {}).get("note_id")
        if not note_id:
            raise Error("Get 新建未返回 ID；不要盲目重试以免重复新建")
        expected_title = args.output_title
    else:
        if not args.target_note_id:
            raise Error("更新 Get 必须明确 --target-note-id 既有精炼笔记")
        note_id = inputs.note_id_string(args.target_note_id)
        if note_id in {task.get("source_note_id"), args.source_note_id}:
            raise Error("禁止更新本任务的源笔记")
        module = load_save_module("update-note.py")
        config = inputs.read_json(task["get_config_path"]) if task.get("get_config_path") else module.load_config()
        content, expected_title = draft, args.output_title
        response = module.update_note(config, module.build_update_payload(note_id, title=expected_title, content=content))
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or not (result.get("code") == 0 or result.get("data")):
            raise Error("Get 更新返回错误；请核对实际返回")
    try:
        verified = verify_readback(module.get_note_detail(config, note_id), expected_title=expected_title, expected_content=content)
    except (OSError, ValueError, TypeError, KeyError):
        return {"ok": False, "destination": args.destination, "note_id": str(note_id), "read_back": "failed",
                "next": "保存已返回目标 ID，但回读失败；先核对该 ID，不要重复新建。未判定故障原因。"}
    return {"ok": verified["read_back"] == "verified", "destination": args.destination, "note_id": str(note_id), **verified}


def deliver(args):
    path, task = load_task(args.task)
    check_long_source(task)
    form_hash = None
    if task.get("review_input"):
        previous = owned_report(task)
        form, form_hash = read_form(path, task)
        revision = form.get("review_revision")
        if type(revision) is not int or revision < 1 or revision != task.get("review_revision"):
            raise Error("缺少当前复核编号或仍用旧编号；阅读最新对照后在 review-input.json 提交对应 review_revision")
        report = reviews.assemble(form, previous, template(task["mode"]), accept=True)
    else:
        report = inputs.read_json(task["review_report"])
    if not task.get("review_binding") or binding(task, report) != task["review_binding"]:
        raise Error("正文、人物映射、参数、选定位置或引文已变化/尚未 review；先重新 review，再完成语义复核")
    materialize(report, read_text(task["source"]), read_text(task["draft"]), verify=True)
    if args.destination == "local":
        if not args.output:
            raise Error("本地交付需要 --output <最终文件路径>")
        output = Path(args.output).resolve()
        reject_alias(output, protected_paths(path, task))
        if output.exists() and not args.replace:
            raise Error("目标文件已存在；保留原稿。只有用户明确授权覆盖时才使用 --replace")
    if task.get("review_input"):
        if read_form(path, task)[1] != form_hash:
            raise Error("复核判断已被其他操作修改；未交付")
        save_owned_report(path, task, report)
    structure_path, final_path = path.parent / "structure.json", path.parent / "validation-report.json"
    save_json(structure_path, analyze_task(task))
    inputs.review_hashes_command(argparse.Namespace(validation_report=str(structure_path), source=task["source"],
        draft=task["draft"], speaker_map=task.get("speaker_map"), review_report=task["review_report"], output=None))
    if task.get("review_input"):
        task["generated_report_sha256"] = inputs.sha_bytes(Path(task["review_report"]).read_bytes())
        save_json(path, task)
    final = analyze_task(task, final=True)
    save_json(final_path, final)
    if not final["final_ready"]:
        return {"ok": False, "operation": "deliver", "issues": final["issues"] + final["final_issues"],
                "next": "按实际问题修改内容/补充复核。需要改稿或改位置时重新 review；不会自动改成通过。"}
    draft = read_text(task["draft"])
    require_final_validation(str(final_path), draft, expected_mode=task["mode"])
    # Recheck immediately before any external side effect.
    if (binding(task, inputs.read_json(task["review_report"])) != task["review_binding"]
            or text_sha256(read_text(task["review_report"])) != final["review_report_sha256"]
            or (form_hash is not None and read_form(path, task)[1] != form_hash)):
        raise Error("保存前输入发生变化；未交付")
    if args.destination == "local":
        backup = None
        if output.exists():
            backup = output.with_name(output.name + ".before-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".bak")
            inputs.write_verified(backup, output.read_bytes())
        data = Path(task["draft"]).read_bytes()
        if inputs.sha_bytes(data) != task["review_binding"]["draft"]:
            raise Error("捕获待保存正文时文件已变化；未交付，需重新 review")
        inputs.write_verified(output, data, replace=args.replace)
        inputs.ensure_exact(output.read_bytes(), data)
        result = {"ok": True, "destination": "local", "output": str(output), "backup": str(backup) if backup else None,
                  "read_back": "verified", "sha256_bytes": inputs.sha_bytes(data), "draft_sha256": text_sha256(draft)}
    else:
        result = save_get(args, task, draft, final_path)
    result.update(operation="deliver", validation_report=str(final_path),
                  meaning="结构与证据检查通过，模型已提交复核结论；程序未验证语义正确性")
    save_json(path.parent / "delivery-result.json", result)
    return result


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    invocation = f'python "{Path(__file__).resolve()}"'
    prep = commands.add_parser("prepare", help="固定原文和本次参数，生成模型输入骨架", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=f"""任务目录必须为空或不存在，真实返回文件/会话日志须在目录之外。输入检查通过才落盘。以下示例请替换笔记ID、路径与调用编号。
真实返回文件：{invocation} prepare --task-dir work/task --mode exchange --note-id 123 --response-file work/real-response.json
内联 MCP 返回：{invocation} prepare --task-dir work/task --mode exchange --note-id 123 --response-file "recorded-session.jsonl" --call-id <Get调用编号>
本地原文：{invocation} prepare --task-dir work/task --mode exchange --source-file work/original.txt
找不到可读返回：{invocation} prepare --task-dir work/task --mode exchange --note-id 123
最后一种复用固定只读 Get 接口；绝不把模型重写的 JSON 当真实返回。""")
    prep.add_argument("--task-dir", "--out-dir", dest="task_dir", required=True)
    source = prep.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-file")
    source.add_argument("--note-id")
    prep.add_argument("--response-file", help="宿主真实返回 JSON；配合 --call-id 时为真实会话 JSONL，不接受模型重抄")
    prep.add_argument("--call-id", help="会话中对应 Get 读取调用的 callId；必须同时提供会话日志路径")
    prep.add_argument("--config", help="现有 Get 凭证文件路径，内容不进入任务记录")
    prep.add_argument("--no-proxy", action="store_true")
    prep.add_argument("--mode", choices=FORMATS, required=True)
    prep.add_argument("--speaker-state", choices=("stable", "unstable"), default="stable")
    prep.add_argument("--people-count", type=int)
    prep.add_argument("--asr-channel-count", type=int)
    prep.add_argument("--expected-duration-seconds", type=int)
    prep.add_argument("--long-source-choice", choices=("keep-one", "split-confirmed"), help="只记录用户明确选择，不替用户决定")
    rev = commands.add_parser("review", help="显示精简对照；不填写语义通过", epilog="例：review --task work/task。直接阅读返回对照，续页加 --page N，完整差异加 --full-diff（只读）。在 review-input.json 填本轮 review_revision、samples.status=pass/fail、risk_checks.status=checked/not_present、preserved_items、必要 semantic_reason 和总 result；有问题如实保留。移除旧问题须在同项 resolved_issues 填 {issue:原问题,handling:具体处置}。不要修改机器报告。人物映射字段见 references/input-preparation.md；无需读源码。")
    rev.add_argument("--task", required=True, help="任务目录或 task.json")
    rev.add_argument("--parts", nargs="+", help="内部写稿分块的有序文件列表；按原字节拼接，块间加两个换行")
    rev.add_argument("--speaker-map")
    rev.add_argument("--speaker-state", choices=("stable", "unstable"))
    rev.add_argument("--people-count", type=int)
    rev.add_argument("--asr-channel-count", type=int)
    rev.add_argument("--long-source-choice", choices=("keep-one", "split-confirmed"))
    rev.add_argument("--show-lines", choices=("source", "draft"), help="只读显示带行号原文或成稿，不运行复核、不改文件")
    rev.add_argument("--start", type=int, default=1)
    rev.add_argument("--end", type=int)
    rev.add_argument("--page", type=int, help="只读查看已生成的复核材料第 N 页，不重置状态")
    rev.add_argument("--full-diff", action="store_true", help="只读显示完整差异，可配合 --page")
    send = commands.add_parser("deliver", help="统一哈希、最终校验、保存和回读", epilog="本地：deliver --task work/task --output final.md；Get 新建：--destination get-new --output-title 标题；更新：--destination get-update --target-note-id ID")
    send.add_argument("--task", required=True)
    send.add_argument("--destination", choices=("local", "get-new", "get-update"), default="local")
    send.add_argument("--output")
    send.add_argument("--replace", action="store_true", help="仅用于用户明确授权的既有成稿；先备份再覆盖")
    send.add_argument("--source-note-id")
    send.add_argument("--target-note-id")
    send.add_argument("--output-title")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = {"prepare": prepare, "review": review, "deliver": deliver}[args.command](args)
    except Error as exc:
        result = {"ok": False, "error": str(exc)}
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        result = {"ok": False, "error": "本地输入或文件格式错误；核对路径和 JSON。原始错误内容未输出，避免泄露凭证"}
    display = result.pop("text", None) if result.get("operation") in {"review", "review-view"} else None
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if display is not None:
        print("\n" + display)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
