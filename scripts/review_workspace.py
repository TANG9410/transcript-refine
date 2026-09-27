"""Model-owned review input and compact evidence views; no semantic verdicts."""

import copy
import difflib
import re

from prepare_transcript_inputs import InputError

PAGE_TARGET = 12000
DECISIONS = ("status", "preserved_items", "unresolved_items", "blocking_issues", "semantic_reason")
PROBLEMS = ("unresolved_items", "blocking_issues")
AUTO_FIELDS = {"source_quote", "draft_quote", "source_quotes", "draft_quotes",
               "source_sha256", "draft_sha256", "speaker_map_sha256"}


def input_template(report):
    result = copy.deepcopy(report)
    result["review_revision"] = None
    result["resolved_issues"] = []
    for key in AUTO_FIELDS:
        result.pop(key, None)
    for group in ("samples", "risk_checks"):
        for item in result[group]:
            for key in AUTO_FIELDS:
                item.pop(key, None)
            item["resolved_issues"] = []
    return result


def validate_form(form, mode, template):
    if not isinstance(form, dict) or form.get("version") != 1 or form.get("mode") != mode:
        raise InputError("review-input.json 版本或稿型不符")
    nodes = [form]
    for group in ("samples", "risk_checks"):
        items = form.get(group)
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise InputError(f"review-input.json {group} 必须为对象列表")
        kinds = [i.get("kind") for i in items]
        expected = [i["kind"] for i in template[group]]
        if len(kinds) != len(expected) or set(kinds) != set(expected):
            raise InputError(f"review-input.json {group} 不得缺项、重复或另加检查类别")
        nodes.extend(items)
    for node in nodes:
        if AUTO_FIELDS.intersection(node):
            raise InputError("review-input.json 只填位置与判断，不接受手写引文或哈希")
        for field in (*PROBLEMS, "preserved_items"):
            values = node.get(field, [])
            if not isinstance(values, list) or not all(isinstance(x, str) and x.strip() for x in values):
                raise InputError(f"{field} 必须为非空文字的列表，空列表可用")
        resolutions = node.get("resolved_issues", [])
        if not isinstance(resolutions, list):
            raise InputError("resolved_issues 必须为列表")
        seen = set()
        for resolution in resolutions:
            if (not isinstance(resolution, dict) or set(resolution) != {"issue", "handling"}
                    or not all(isinstance(resolution[k], str) and resolution[k].strip() for k in resolution)):
                raise InputError("问题处置须填写 resolved_issues: [{issue: 原问题, handling: 具体处置}]")
            if resolution["issue"] in seen:
                raise InputError("同一检查项的问题处置不得重复")
            seen.add(resolution["issue"])


def union(left, right):
    result = list(left)
    for value in right:
        if value not in result:
            result.append(value)
    return result


def merge_node(old, submitted, target, accept):
    """Preserve outstanding issues; only explicit issue-by-issue handling resolves one."""
    if not accept:
        for field in PROBLEMS:
            target[field] = union(old.get(field, []), submitted.get(field, []))
        target["preserved_items"] = union(old.get("preserved_items", []), submitted.get("preserved_items", []))
        target["resolved_issues"] = copy.deepcopy(old.get("resolved_issues", []))
        return
    remaining = submitted.get("unresolved_items", []) + submitted.get("blocking_issues", [])
    original = old.get("unresolved_items", []) + old.get("blocking_issues", [])
    resolutions = {r["issue"]: r["handling"] for r in submitted.get("resolved_issues", [])}
    history = {r["issue"]: r["handling"] for r in old.get("resolved_issues", [])}
    for issue in original:
        if issue not in remaining and issue not in resolutions:
            raise InputError("已有问题被移除但没有逐条处置说明：" + issue)
    for issue, handling in resolutions.items():
        if issue in remaining:
            raise InputError("问题不能同时标为未解决和已处置：" + issue)
        if issue not in original and issue not in history:
            raise InputError("处置说明未对应本项已记录的问题：" + issue)
        history[issue] = handling
    for field in DECISIONS:
        if field in submitted:
            target[field] = copy.deepcopy(submitted[field])
    target["resolved_issues"] = [{"issue": k, "handling": v} for k, v in history.items()]
    target.setdefault("preserved_items", [])
    for issue, handling in history.items():
        entry = f"已处置：{issue}；处理：{handling}"
        if entry not in target["preserved_items"]:
            target["preserved_items"].append(entry)


def assemble(form, previous, template, accept=False):
    result = copy.deepcopy(previous or template)
    result["result"] = form.get("result", "pending") if accept else result.get("result", "pending")
    merge_node(previous or {}, form, result, accept)
    for group in ("samples", "risk_checks"):
        submitted = {i["kind"]: i for i in form[group]}
        old = {i["kind"]: i for i in (previous or template)[group]}
        for item in result[group]:
            value = submitted[item["kind"]]
            for side in ("source", "draft"):
                key = side + ("_locator" if group == "samples" else "_locators")
                item[key] = copy.deepcopy(value.get(key))
            merge_node(old[item["kind"]], value, item, accept)
    return result


def ranges_for(report, side):
    values = []
    for group in ("samples", "risk_checks"):
        for item in report[group]:
            locators = [item[side + "_locator"]] if group == "samples" else item[side + "_locators"]
            for loc in locators:
                values.append((loc["start"], loc["end"], item["kind"]))
    return values


def appendix_start(lines):
    for index, line in enumerate(lines, 1):
        if re.match(r"^##\s+.*(?:附录|人物关系|疑似识别错误|精炼.*说明|修正说明|时间覆盖检查)", line):
            return index
    return None


def compact_sections(report, source, draft, structure, revision):
    index = ["# 原文与成稿集中复核", f"复核编号：{revision}",
             "程序提取证据；阅读完整问答，核对正文及附录的行动人、条件与确定程度，再提交判断。",
             "结构检查：" + ("通过" if structure["ok"] else "；".join(structure["issues"]))]
    if structure.get("warnings"):
        index.append("提示：" + "；".join(structure["warnings"]))
    for group in ("samples", "risk_checks"):
        for item in report[group]:
            refs = []
            for side, label in (("source", "原文"), ("draft", "成稿")):
                locs = [item[side + "_locator"]] if group == "samples" else item[side + "_locators"]
                refs.append(label + " " + (", ".join(f"{r['start']}-{r['end']}" for r in locs) or "未选"))
            index.append("- " + item["kind"] + "：" + "；".join(refs))
            for issue in item.get("unresolved_items", []) + item.get("blocking_issues", []):
                index.append("  待处置：" + issue)
    for issue in report.get("unresolved_items", []) + report.get("blocking_issues", []):
        index.append("待处置：" + issue)
    sections = ["\n".join(index)]
    for side, text, label in (("source", source, "原文"), ("draft", draft, "成稿")):
        lines = text.splitlines()
        raw = ranges_for(report, side)
        if side == "draft":
            start = appendix_start(lines)
            if start:
                raw.append((start, len(lines), "附录与正文一致性（纳入对应风险项判断）"))
        expanded = sorted((max(1, a - 2), min(len(lines), b + 2), {kind}) for a, b, kind in raw)
        merged = []
        for a, b, kinds in expanded:
            if merged and a <= merged[-1][1] + 1:
                pa, pb, pk = merged[-1]
                merged[-1] = (pa, max(pb, b), pk | kinds)
            else:
                merged.append((a, b, kinds))
        for a, b, kinds in merged:
            # The index already associates every range with its checks. Keep blank
            # paragraph boundaries for pagination, without repeating empty row labels.
            header = f"## {label} {a}-{b} 行"
            sections.append(header + "\n" + "\n".join(
                f"{n}: {lines[n-1]}" if lines[n-1].strip() else "" for n in range(a, b + 1)))
    return sections


def full_diff_sections(report, source, draft):
    sections = ["# 完整文字差异（不自动判定合理性）"]
    for group in ("samples", "risk_checks"):
        for item in report[group]:
            before = item.get("source_quote", "\n".join(item.get("source_quotes", [])))
            after = item.get("draft_quote", "\n".join(item.get("draft_quotes", [])))
            diff = "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile="原文", tofile="成稿", lineterm=""))
            sections.append("## " + item["kind"] + "\n" + (diff or "选定文字一致"))
    return sections


def paginate(sections, target=PAGE_TARGET):
    """Keep complete sections, then complete paragraphs; oversized fragments remain whole."""
    pages, current = [], ""
    for section in sections:
        pieces = [section]
        if len(section) > target:
            title, _, body = section.partition("\n")
            heading, piece, pieces = title + "（续页）", title, []
            for fragment in re.split(r"\n\s*\n", body):
                if len(piece) + len(fragment) + 2 > target and piece not in (title, heading):
                    pieces.append(piece)
                    piece = heading
                piece += "\n\n" + fragment
            pieces.append(piece)
        for piece in pieces:
            if current and len(current) + len(piece) + 2 > target:
                pages.append(current)
                current = ""
            current += ("\n\n" if current else "") + piece
    if current:
        pages.append(current)
    return pages or ["无对照材料"]


def page_result(sections, page, revision):
    pages = paginate(sections)
    if not isinstance(page, int) or page < 1 or page > len(pages):
        raise InputError(f"页码越界；共有 {len(pages)} 页")
    return {"review_revision": revision, "page": page, "total_pages": len(pages),
            "remaining_pages": len(pages) - page, "text": pages[page - 1],
            "oversized_complete_fragment": len(pages[page - 1]) > PAGE_TARGET}
