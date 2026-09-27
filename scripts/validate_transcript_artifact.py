#!/usr/bin/env python3
"""三种逐字稿主稿的确定性校验器。"""

import argparse
import json
import re
import sys
from pathlib import Path

from note_io_common import json_sha256, text_sha256


TIME = r"\d{2}:\d{2}(?::\d{2})?"
TOPIC_RE = re.compile(rf"^##\s+(.+?)\s+[\[（(]({TIME})\s*[-~～—–]\s*({TIME})[\]）)]\s*$")
SPEAKER_RE = re.compile(r"^\*\*(.+?)[：:]\*\*")
TIME_TOKEN_RE = re.compile(rf"\[{TIME}\]")
SOURCE_TIME_MARKER_RE = re.compile(rf"(?m)(?:\[{TIME}\]|^\s*(?:[-*]\s*)?{TIME}\s*$|https://getnotes\.seek:\d+)")
STANDALONE_TIME_RE = re.compile(rf"^\s*\*{{0,2}}\[{TIME}\]\*{{0,2}}\s*$")
EVIDENCE_ANCHOR_RE = re.compile(r"【(?:(?:S\d{2,})｜)?[^】｜]+｜[^】]+】")
SOURCE_ID_RE = re.compile(r"\bS\d{2,}\b")
LOW_CONFIDENCE_RE = re.compile(r"^(?:说话人[A-Z0-9]+|低置信(?:说话人)?[A-Z0-9]+)$")
MARKED_LOW_CONFIDENCE_RE = re.compile(
    r"(?P<label>[^（）]+)（(?:归属待核|低置信(?:，原标签(?P<original>[^（）]+))?)）"
)
FILLERS = ("嗯嗯嗯", "对对对", "我我我", "这个这个这个", "就是就是就是")
ASR_NOISE = ("G U", "G E U", "指定发生单位", "SARS", "萨克斯", "董成弟")
META_NARRATION_TERMS = ("原始转写中", "本稿", "本段", "为避免误传", "随后沟通了一项")
THIRD_PERSON_SUMMARY_RE = re.compile(
    r"^(?:客户|销售|对方|甲方|乙方|说话人[A-Z0-9]+).{0,12}(?:介绍了|表示|认为|说明|提到|沟通了)"
)
REQUIRED_REVIEW_SAMPLES = {"opening", "middle", "ending"}
REQUIRED_RISK_CHECKS = {
    "numbers_prices",
    "questions_objections",
    "commitments_next_steps",
    "speaker_asr_uncertainty",
    "technical_interlude",
}
MIN_EVIDENCE_CHARS = 4
VALIDATION_REPORT_CONTRACT = "transcript-refine-v3-final-ready-v1"
RISK_SIGNAL_RE = {
    "numbers_prices": re.compile(r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万亿]+)(?:来|多|余|几)?\s*(?:千|万|亿|元|块(?:钱)?|折|个?点|%|％)|百分之[零〇一二两三四五六七八九十百\d]+(?:点[零〇一二两三四五六七八九\d]+)?|(?:几|上)(?:千|万|亿)"),
    "questions_objections": re.compile(r"[？?]|为什么|问题|担心|顾虑|异议|成本|不决定|不会现在|限制|很少|只是|但是|但我|不大|不可能|没用|再想想|不一定"),
    "commitments_next_steps": re.compile(r"下一步|后续|后面|跟进|联系|加.*微信|发过去|发送|确认|安排|对接|承诺|再聊|明天|最晚|交付|再考虑|回头|决定"),
    "speaker_asr_uncertainty": re.compile(r"待确认|低置信|不确定|识别错误|ASR|近音|疑似"),
    "technical_interlude": re.compile(r"技术|接口|API|系统|插件|报错|配置|代码|模型|算法|脚本|数据库|程序|按钮|调试|验收|设备|通讯|控制柜|故障|现场|自动化"),
}
EXCHANGE_APPENDIX_RE = re.compile(
    r"^##\s+(?:附录(?:\b|[：:])|(?:📊\s*)?(?:销售复盘观察|销售观察|复盘观察)|"
    r"时间覆盖检查|人物关系(?:与低置信说明)?|疑似识别错误与(?:修正说明|待确认项)|精炼说明)"
)
EXCHANGE_METADATA_FIELDS = ("会议时间", "参会人员", "原始笔记")
EXCHANGE_METADATA_RE = re.compile(r"^>\s*(会议时间|参会人员|原始笔记)\s*[：:]\s*(.*?)\s*$")


def read_text_file(path):
    return Path(path).read_text(encoding="utf-8-sig")


def read_json_file(path):
    return json.loads(read_text_file(path))


def section(text, heading):
    match = re.search(rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)", text)
    return match.group(1).strip() if match else None


def parse_time(value):
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        hour, minute, second = 0, parts[0], parts[1]
    else:
        hour, minute, second = parts
    if minute >= 60 or second >= 60:
        raise ValueError("minute/second out of range")
    return hour * 3600 + minute * 60 + second


def retention_ratio(draft_text, source_text):
    """Return a descriptive metric only; it never decides output quality."""
    if not source_text:
        return None
    return len(draft_text) / len(source_text)


def retention_warning(draft_text, source_text, warn_near_verbatim=True):
    """Backward-compatible wrapper: 4.0 never emits ratio-derived warnings."""
    del warn_near_verbatim
    return retention_ratio(draft_text, source_text), None


def parse_source_span(value, prefix, issues):
    if not isinstance(value, dict):
        issues.append(f"{prefix} source_span must be an object")
        return None
    kind = value.get("kind")
    start, end = value.get("start"), value.get("end")
    if kind == "time":
        if not isinstance(start, str) or not isinstance(end, str):
            issues.append(f"{prefix} time source_span needs string start/end")
            return None
        try:
            parsed = (kind, parse_time(start), parse_time(end))
        except ValueError:
            issues.append(f"{prefix} time source_span is invalid")
            return None
    elif kind == "line":
        if type(start) is not int or type(end) is not int or start < 1:
            issues.append(f"{prefix} line source_span needs positive integer start/end")
            return None
        parsed = (kind, start, end)
    else:
        issues.append(f"{prefix} source_span kind must be time or line")
        return None
    if parsed[2] < parsed[1]:
        issues.append(f"{prefix} source_span end precedes start")
        return None
    return parsed


def exchange_body_lines(draft_text):
    """Return only dialogue-body lines, excluding recognized appendices."""
    lines = []
    for line in draft_text.splitlines():
        if EXCHANGE_APPENDIX_RE.match(line.strip()):
            break
        lines.append(line)
    return lines


def validate_exchange_metadata(draft_text):
    """Check the small provenance header required by newly prepared exchange tasks."""
    issues = []
    lines = draft_text.splitlines()
    title_index = next((index for index, line in enumerate(lines) if line.startswith("# ")), None)
    if title_index is None:
        return ["exchange draft is missing a level-1 title"]
    first_topic = next((index for index in range(title_index + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
    found = {}
    for index in range(title_index + 1, first_topic):
        match = EXCHANGE_METADATA_RE.match(lines[index].strip())
        if match:
            key, value = match.group(1), match.group(2).strip()
            if value:
                if key in found:
                    issues.append(f"exchange metadata duplicated: {key}")
                found[key] = index + 1
            else:
                issues.append(f"exchange metadata is empty: {key}")
    missing = [key for key in EXCHANGE_METADATA_FIELDS if key not in found]
    if missing:
        issues.append(f"exchange metadata missing required fields: {', '.join(missing)}")
    ordered = [found[key] for key in EXCHANGE_METADATA_FIELDS if key in found]
    if ordered != sorted(ordered):
        issues.append("exchange metadata fields must be ordered: 会议时间、参会人员、原始笔记")
    return issues


def is_low_confidence_label(value):
    """Accept original/role labels that explicitly retain attribution uncertainty."""
    if not isinstance(value, str) or any(char in value for char in "\r\n\v\f\x85\u2028\u2029"):
        return False
    value = value.strip()
    if LOW_CONFIDENCE_RE.fullmatch(value):
        return True
    match = MARKED_LOW_CONFIDENCE_RE.fullmatch(value)
    return bool(match and match["label"].strip() and (match["original"] is None or match["original"].strip()))


def validate_speaker_map(
    draft_text,
    speaker_map,
    people_count=None,
    asr_channel_count=None,
    speaker_state="stable",
    final_check=False,
):
    issues = []
    required = speaker_state == "unstable"
    stats = {
        "speaker_map_required": required,
        "speaker_map_decision_basis": "semantic attribution instability, not speaker count",
    }
    if speaker_state not in {"stable", "unstable"}:
        issues.append("speaker_state must be stable or unstable")
    if speaker_map is None:
        if required:
            issues.append("unstable speaker attribution requires speaker map")
        return issues, stats
    if not isinstance(speaker_map, dict):
        return ["speaker map must be a JSON object"], stats
    version = speaker_map.get("version")
    if version not in {1, 2}:
        issues.append("speaker map version must be 1 or 2")
    if final_check and required and version != 2:
        issues.append("unstable final check requires segmented speaker map version 2")
    if speaker_map.get("invalidated") is True:
        issues.append("speaker map has been invalidated and must be rebuilt")
    map_people = speaker_map.get("people_count")
    map_channels = speaker_map.get("asr_channel_count")
    if not isinstance(map_people, int) or map_people < 2:
        issues.append("speaker map people_count must be an integer >= 2")
    if not isinstance(map_channels, int) or map_channels < 1:
        issues.append("speaker map asr_channel_count must be an integer >= 1")
    if people_count is not None and map_people != people_count:
        issues.append("speaker map people_count does not match command input")
    if asr_channel_count is not None and map_channels != asr_channel_count:
        issues.append("speaker map asr_channel_count does not match command input")

    mappings = speaker_map.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        issues.append("speaker map mappings must be a non-empty list")
        return issues, stats
    output_labels, label_counts, parsed_spans = set(), {}, {}
    unresolved_labels = set()
    unresolved_count = locked_count = 0
    for index, item in enumerate(mappings, start=1):
        prefix = f"speaker map item {index}"
        if not isinstance(item, dict):
            issues.append(f"{prefix} must be an object")
            continue
        asr_label = item.get("asr_label")
        output_label = item.get("output_label")
        person = item.get("person")
        evidence = item.get("evidence")
        confidence = item.get("confidence")
        status = item.get("status")
        if not isinstance(asr_label, str) or not asr_label.strip():
            issues.append(f"{prefix} missing asr_label")
            asr_label = None
        else:
            asr_label = asr_label.strip()
            label_counts[asr_label] = label_counts.get(asr_label, 0) + 1
        if not isinstance(output_label, str) or not output_label.strip():
            issues.append(f"{prefix} missing output_label")
            continue
        output_label = output_label.strip()
        output_labels.add(output_label)
        if not isinstance(evidence, list) or not any(isinstance(x, str) and x.strip() for x in evidence):
            issues.append(f"{prefix} needs non-empty evidence")
        if confidence not in {"high", "medium", "low", "unknown"}:
            issues.append(f"{prefix} has invalid confidence")
        if status == "locked":
            locked_count += 1
            if not isinstance(person, str) or not person.strip():
                issues.append(f"{prefix} locked mapping requires person")
            elif output_label != person.strip():
                issues.append(f"{prefix} locked output_label must equal person")
            if confidence not in {"high", "medium"}:
                issues.append(f"{prefix} locked mapping needs high or medium confidence")
        elif status == "unresolved":
            unresolved_count += 1
            unresolved_labels.add(output_label)
            if person not in (None, ""):
                issues.append(f"{prefix} unresolved mapping must not set person")
            if not is_low_confidence_label(item.get("output_label")):
                issues.append(f"{prefix} unresolved mapping must use a low-confidence label")
            if confidence not in {"low", "unknown"}:
                issues.append(f"{prefix} unresolved mapping needs low or unknown confidence")
            candidates = item.get("candidates")
            if not isinstance(candidates, list) or not any(isinstance(x, str) and x.strip() for x in candidates):
                issues.append(f"{prefix} unresolved candidates must be a non-empty list")
        else:
            issues.append(f"{prefix} status must be locked or unresolved")
        if version == 2 and item.get("source_span") is not None:
            parsed = parse_source_span(item.get("source_span"), prefix, issues)
            if parsed is not None and asr_label:
                parsed_spans.setdefault(asr_label, []).append((parsed, prefix))
        elif version == 2 and status == "unresolved":
            issues.append(f"{prefix} unresolved version 2 mapping requires source_span")

    if version == 2:
        for label, count in label_counts.items():
            spans = parsed_spans.get(label, [])
            if count > 1 and len(spans) != count:
                issues.append(f"repeated ASR label {label!r} requires source_span on every mapping")
            by_kind = {}
            for span, prefix in spans:
                by_kind.setdefault(span[0], []).append((span[1], span[2], prefix))
            for values in by_kind.values():
                values.sort()
                for previous, current in zip(values, values[1:]):
                    if current[0] < previous[1]:
                        issues.append(f"overlapping source spans: {previous[2]} and {current[2]}")

    draft_labels = set()
    for line in exchange_body_lines(draft_text):
        match = SPEAKER_RE.match(line.strip())
        if match:
            draft_labels.add(match.group(1).strip())
    unknown_labels = sorted(draft_labels - output_labels)
    if unknown_labels:
        issues.append(f"draft speaker labels missing from speaker map: {unknown_labels}")
    missing_unresolved = sorted(unresolved_labels - draft_labels)
    if version == 2 and missing_unresolved:
        issues.append(f"unresolved source spans were not preserved with low-confidence labels: {missing_unresolved}")
    stats.update(
        {
            "speaker_map_present": True,
            "speaker_map_version": version,
            "speaker_map_locked": locked_count,
            "speaker_map_unresolved": unresolved_count,
        }
    )
    return issues, stats


def analyze_exchange(draft_text, source_text=None, expected_duration=None, require_metadata=False):
    issues, warnings, body_lines, topics, plain_topics = [], [], [], [], []
    if require_metadata:
        issues.extend(validate_exchange_metadata(draft_text))
    source_has_time = None if source_text is None else bool(SOURCE_TIME_MARKER_RE.search(source_text))
    body_lines = exchange_body_lines(draft_text)
    body_text = "\n".join(body_lines)
    for line_no, line in enumerate(body_lines, start=1):
        if not line.startswith("## "):
            continue
        match = TOPIC_RE.match(line)
        if not match:
            if source_has_time is False:
                plain_topics.append(line_no)
            else:
                issues.append(f"line {line_no}: topic heading missing valid time range")
            continue
        if source_has_time is False:
            issues.append(f"line {line_no}: source has no timestamps; draft must not invent a time range")
            continue
        try:
            start, end = parse_time(match.group(2)), parse_time(match.group(3))
        except ValueError:
            issues.append(f"line {line_no}: invalid minute or second")
            continue
        if end < start:
            issues.append(f"line {line_no}: topic end precedes start")
        topics.append((line_no, start, end))
    if not topics and not plain_topics:
        issues.append("no topic headings found")
    for previous, current in zip(topics, topics[1:]):
        if current[1] < previous[1]:
            issues.append(f"line {current[0]}: topic start time is not monotonic")
    if topics and expected_duration is not None:
        if topics[0][1] > 60:
            warnings.append("timeline starts more than 60 seconds after source start")
        if abs(topics[-1][2] - expected_duration) > 120:
            warnings.append("last topic end differs from expected duration by over 120 seconds")
    standalone, inline = [], []
    for number, line in enumerate(body_lines, start=1):
        if STANDALONE_TIME_RE.match(line):
            standalone.append(number)
        elif TIME_TOKEN_RE.search(line) and not TOPIC_RE.match(line):
            inline.append(number)
    if standalone:
        issues.append(f"standalone timestamp lines: {standalone[:10]}")
    if inline:
        issues.append(f"inline timestamps: {inline[:10]}")
    adjacent, last_speaker = 0, None
    for block in re.split(r"\r?\n\s*\r?\n", body_text):
        stripped = block.strip()
        if stripped.startswith("## "):
            last_speaker = None
            continue
        match = SPEAKER_RE.match(stripped)
        if not match:
            if stripped:
                last_speaker = None
            continue
        speaker = match.group(1)
        if speaker == last_speaker:
            adjacent += 1
        last_speaker = speaker
    if adjacent:
        issues.append(f"adjacent same-speaker blocks: {adjacent}")
    filler_hits = {item: body_text.count(item) for item in FILLERS if item in body_text}
    noise_hits = {item: body_text.count(item) for item in ASR_NOISE if item in body_text}
    if filler_hits:
        warnings.append(f"filler runs found: {filler_hits}")
    if noise_hits:
        warnings.append(f"known ASR noise found: {noise_hits}")
    meta_hits = []
    for line_no, line in enumerate(body_lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = SPEAKER_RE.match(stripped)
        if not match:
            found = [term for term in META_NARRATION_TERMS if term in stripped]
            if THIRD_PERSON_SUMMARY_RE.search(stripped):
                found.append("第三人称概括")
            if found:
                meta_hits.append({"line": line_no, "speaker": None, "terms": sorted(set(found))})
            continue
        speaker = match.group(1).strip()
        content = stripped[match.end():].strip()
        found = [term for term in META_NARRATION_TERMS if term in content]
        if any(content.startswith(f"{speaker}{verb}") for verb in ("介绍了", "表示", "认为", "说明", "提到")):
            found.append(f"{speaker}第三人称概括")
        if found:
            meta_hits.append({"line": line_no, "speaker": speaker, "terms": sorted(set(found))})
    if meta_hits:
        issues.append(f"exchange body contains editor narration: {meta_hits[:10]}")
    ratio = retention_ratio(body_text, source_text)
    return issues, warnings, {
        "topic_count": len(topics) + len(plain_topics),
        "retention_ratio": ratio,
        "source_chars": len(source_text) if source_text is not None else None,
        "draft_body_chars": len(body_text),
        "source_timestamp_count": len(SOURCE_TIME_MARKER_RE.findall(source_text or "")),
        "draft_speaker_block_count": sum(1 for line in body_lines if SPEAKER_RE.match(line.strip())),
        "editor_narration_hits": len(meta_hits),
    }


def review_rows(section_text):
    if not section_text:
        return []
    return [line.strip() for line in section_text.splitlines() if line.strip().startswith("- ") and "无" not in line.strip()]


def analyze_review(draft_text, source_text=None, expected_duration=None):
    del source_text, expected_duration
    issues, warnings = [], []
    headings = ("背景与目标", "已确认事实", "主要判断与分歧", "风险与待确认", "行动项")
    sections = {name: section(draft_text, name) for name in headings}
    for name, content in sections.items():
        if content is None:
            issues.append(f"missing required section: {name}")
    for name in ("主要判断与分歧", "风险与待确认"):
        for line in review_rows(sections.get(name)):
            if not EVIDENCE_ANCHOR_RE.search(line):
                issues.append(f"{name} item missing evidence anchor: {line[:80]}")
    action = sections.get("行动项") or ""
    if action and not all(term in action for term in ("事项", "责任人", "时间", "状态", "证据")):
        issues.append("action table missing required columns")
    for line in action.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or re.match(r"^\|?\s*[-:]+", stripped):
            continue
        if "事项" in stripped and "责任人" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 5 and not EVIDENCE_ANCHOR_RE.search(cells[-1]):
            issues.append(f"action item missing evidence anchor: {stripped[:80]}")
    coverage = section(draft_text, "来源覆盖") or ""
    coverage_ids = set(SOURCE_ID_RE.findall(coverage))
    anchors = EVIDENCE_ANCHOR_RE.findall(draft_text)
    if not anchors:
        issues.append("no evidence anchors found")
    return issues, warnings, {"evidence_anchor_count": len(anchors), "coverage_source_ids": len(coverage_ids)}


def analyze_learning(draft_text, source_text=None, expected_duration=None):
    del expected_duration
    issues, warnings = [], []
    required = ("基本信息", "课程地图", "源内容覆盖表")
    sections = {name: section(draft_text, name) for name in required}
    for name, content in sections.items():
        if content is None:
            issues.append(f"missing required section: {name}")
    fixed = set(required)
    chapters = [m.group(1).strip() for m in re.finditer(r"(?m)^##\s+(.+?)\s*$", draft_text) if m.group(1).strip() not in fixed]
    if len(chapters) < 2:
        issues.append("learning draft needs at least two detailed knowledge chapters")
    coverage, rows = sections.get("源内容覆盖表") or "", []
    for line in coverage.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "原文位置/话题" in stripped or "源段" in stripped or re.match(r"^\|?\s*[-:]+", stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 4 and SOURCE_ID_RE.fullmatch(cells[0]):
            locator, action, reason = cells[0], cells[2], cells[3]
        elif len(cells) >= 3:
            locator, action, reason = cells[0], cells[1], cells[2]
        else:
            continue
        rows.append((locator, action, reason))
        if action not in {"详细保留", "压缩交代", "删除", "删除或从略"}:
            issues.append(f"invalid coverage action for {locator}: {action}")
        if action in {"删除", "删除或从略"} and len(reason) < 2:
            issues.append(f"deleted source segment lacks reason: {locator}")
    if not rows:
        issues.append("source coverage table has no valid rows")
    covered_ids = set()
    for locator, _, _ in rows:
        covered_ids.update(SOURCE_ID_RE.findall(locator))
    missing = sorted(set(SOURCE_ID_RE.findall(source_text or "")) - covered_ids)
    if missing:
        issues.append(f"source ids missing from coverage table: {missing}")
    signals = {"案例": ("案例", "举例", "比如"), "数据": ("数据", "%", "万", "元"), "限制": ("限制", "边界", "前提", "条件", "不能"), "问答": ("问答", "Q&A", "提问", "问题")}
    for label, terms in signals.items():
        if source_text and any(term in source_text for term in terms) and not any(term in draft_text for term in terms):
            warnings.append(f"source contains {label} signals but draft does not")
    promo_terms = ("门票", "会员促销", "下单", "报名", "老板节", "粉丝灯牌")
    if sum((source_text or "").count(term) for term in promo_terms) >= 3 and not ("删除" in coverage and "促销" in coverage):
        warnings.append("source has repeated promotion signals but coverage table lacks a promotion deletion reason")
    ratio = retention_ratio(draft_text, source_text)
    return issues, warnings, {"knowledge_chapter_count": len(chapters), "coverage_rows": len(rows), "retention_ratio": ratio}


def has_locator(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value)
    return False


def resolve_line_locator(text, locator):
    """Extract an inclusive, 1-based line range; reject invalid/blank ranges.

    Public API used by transcript_task. Newline normalization matches splitlines;
    the source file itself is never changed. Errors are always ValueError.
    """
    if not isinstance(locator, dict) or locator.get("kind") != "line":
        raise ValueError("locator must be an object with kind=line")
    start, end = locator.get("start"), locator.get("end")
    if type(start) is not int or type(end) is not int or start < 1 or end < start:
        raise ValueError("line locator needs integers 1 <= start <= end (bool is invalid)")
    if not isinstance(text, str):
        raise ValueError("line locator needs text content")
    lines = text.splitlines()
    if end > len(lines):
        raise ValueError(f"line locator exceeds content length ({len(lines)} lines)")
    excerpt = "\n".join(lines[start - 1:end])
    if not excerpt.strip():
        raise ValueError("line locator resolves to empty content")
    return excerpt


def validate_locator_evidence(text, locator, quote, prefix, issues, counts):
    """Validate a structured range or record an explicitly unverified legacy label."""
    if isinstance(locator, str) and locator.strip():
        counts["legacy_free_text"] += 1
        return
    try:
        excerpt = resolve_line_locator(text, locator)
    except ValueError as exc:
        issues.append(f"{prefix}: {exc}")
        counts["invalid"] += 1
        return
    counts["structured_line"] += 1
    if not evidence_present(excerpt, quote):
        issues.append(f"{prefix} quote must exist inside its line range")
        counts["invalid"] += 1


def validate_locator_list(text, locators, quotes, prefix, issues, counts):
    """Structured and mixed lists must pair each quote with exactly one locator.

    Historical free-text lists were not necessarily one-to-one. Preserve that
    old contract while never claiming their positions were verified.
    """
    if not isinstance(locators, list) or not isinstance(quotes, list):
        issues.append(f"{prefix} locators and quotes must be lists")
        counts["invalid"] += 1
        return
    if not locators and not quotes:
        return
    if locators and all(isinstance(x, str) and x.strip() for x in locators):
        counts["legacy_free_text"] += len(locators)
        return
    if len(locators) != len(quotes):
        issues.append(f"{prefix} needs one locator for every quote")
        counts["invalid"] += 1
    for index, locator in enumerate(locators):
        quote = quotes[index] if index < len(quotes) else None
        validate_locator_evidence(text, locator, quote, f"{prefix}[{index + 1}]", issues, counts)


def compact_text(value):
    return re.sub(r"\s+", "", value or "")


def evidence_present(haystack, needle):
    if not isinstance(needle, str) or len(compact_text(needle)) < MIN_EVIDENCE_CHARS:
        return False
    return compact_text(needle) in compact_text(haystack)


def list_has_evidence(haystack, values):
    if not isinstance(values, list):
        return False
    return bool(values) and all(evidence_present(haystack, value) for value in values)


def list_has_risk_signal(kind, values, speaker_map_sha256=None):
    if not isinstance(values, list):
        return False
    if kind == "speaker_asr_uncertainty" and speaker_map_sha256 is not None:
        pattern = re.compile(r"(?:说话人[A-Z0-9]+|低置信|不确定|ASR|待确认|疑似)")
    else:
        pattern = RISK_SIGNAL_RE.get(kind)
    return bool(pattern and any(isinstance(value, str) and pattern.search(value) for value in values))


def risk_signal_present(kind, source_text, speaker_map_sha256=None):
    if kind == "speaker_asr_uncertainty" and speaker_map_sha256 is not None:
        return True
    pattern = RISK_SIGNAL_RE.get(kind)
    return bool(pattern and pattern.search(source_text or ""))


def semantic_reason_present(value):
    # Only checks that an explanation was supplied; its truth needs source review.
    generic = {"已核对", "已复核", "词表误报", "语义判断", "不适用", "存在风险", "无风险", "checked", "not_present"}
    return isinstance(value, str) and bool(value.strip()) and value.strip().rstrip("。.!！") not in generic


def validate_review_report(review_report, mode, source_text, draft_text, speaker_map_sha256=None):
    issues = []
    locator_counts = {"structured_line": 0, "legacy_free_text": 0, "invalid": 0}
    stats = {
        "review_report_present": review_report is not None,
        "source_sha256": text_sha256(source_text or ""),
        "draft_sha256": text_sha256(draft_text),
        "speaker_map_sha256": speaker_map_sha256,
    }
    if not isinstance(review_report, dict):
        return ["final check requires review report JSON object"], stats
    if review_report.get("version") != 1:
        issues.append("review report version must be 1")
    if review_report.get("mode") != mode:
        issues.append("review report mode does not match validator mode")
    if review_report.get("source_sha256") != stats["source_sha256"]:
        issues.append("review report source_sha256 mismatch")
    if review_report.get("draft_sha256") != stats["draft_sha256"]:
        issues.append("review report draft_sha256 mismatch")
    if speaker_map_sha256 is not None and review_report.get("speaker_map_sha256") != speaker_map_sha256:
        issues.append("review report speaker_map_sha256 mismatch")
    if speaker_map_sha256 is None and review_report.get("speaker_map_sha256") not in (None, ""):
        issues.append("review report names a speaker map that was not validated")

    for field in ("unresolved_items", "blocking_issues"):
        values = review_report.get(field, [])
        if not isinstance(values, list):
            issues.append(f"review report {field} must be a list")
        elif values:
            issues.append(f"review report has non-empty {field}")

    samples = review_report.get("samples")
    seen_samples = set()
    if not isinstance(samples, list):
        issues.append("review report samples must be a list")
        samples = []
    for index, item in enumerate(samples, start=1):
        prefix = f"review sample {index}"
        if not isinstance(item, dict):
            issues.append(f"{prefix} must be an object")
            continue
        kind = item.get("kind")
        if isinstance(kind, str):
            seen_samples.add(kind)
        if not has_locator(item.get("source_locator")):
            issues.append(f"{prefix} missing source_locator")
        if not has_locator(item.get("draft_locator")):
            issues.append(f"{prefix} missing draft_locator")
        source_quote = item.get("source_quote")
        draft_quote = item.get("draft_quote")
        if not evidence_present(source_text, source_quote):
            issues.append(f"{prefix} source_quote must be a real source excerpt")
        if not evidence_present(draft_text, draft_quote):
            issues.append(f"{prefix} draft_quote must be a real draft excerpt")
        validate_locator_evidence(
            source_text, item.get("source_locator"), source_quote,
            f"{prefix} source_locator", issues, locator_counts,
        )
        validate_locator_evidence(
            draft_text, item.get("draft_locator"), draft_quote,
            f"{prefix} draft_locator", issues, locator_counts,
        )
        preserved = item.get("preserved_items")
        if not isinstance(preserved, list) or not any(isinstance(x, str) and x.strip() for x in preserved):
            issues.append(f"{prefix} needs non-empty preserved_items")
        unresolved = item.get("unresolved_items", [])
        if not isinstance(unresolved, list):
            issues.append(f"{prefix} unresolved_items must be a list")
        elif unresolved:
            issues.append(f"{prefix} has unresolved items")
        blocking = item.get("blocking_issues")
        if not isinstance(blocking, list):
            issues.append(f"{prefix} blocking_issues must be a list")
        elif blocking:
            issues.append(f"{prefix} has blocking issues")
        if item.get("status") != "pass":
            issues.append(f"{prefix} status must be pass")
    missing_samples = sorted(REQUIRED_REVIEW_SAMPLES - seen_samples)
    if missing_samples:
        issues.append(f"review report missing source-aligned samples: {missing_samples}")

    risk_checks = review_report.get("risk_checks")
    seen_risks = set()
    duplicated_risks = set()
    if not isinstance(risk_checks, list):
        issues.append("review report risk_checks must be a list")
        risk_checks = []
    for index, item in enumerate(risk_checks, start=1):
        prefix = f"risk check {index}"
        if not isinstance(item, dict):
            issues.append(f"{prefix} must be an object")
            continue
        kind, status = item.get("kind"), item.get("status")
        if isinstance(kind, str):
            if kind in seen_risks:
                duplicated_risks.add(kind)
            seen_risks.add(kind)
        if status not in {"checked", "not_present"}:
            issues.append(f"{prefix} status must be checked or not_present")
        semantic_reason = semantic_reason_present(item.get("semantic_reason"))
        if status == "not_present" and risk_signal_present(kind, source_text, speaker_map_sha256):
            if not semantic_reason:
                issues.append(f"{prefix} cannot be not_present without a concrete semantic_reason for detected {kind} signals")
            if not list_has_evidence(source_text, item.get("source_quotes")):
                issues.append(f"{prefix} not_present override needs real source_quotes for every excerpt")
            locators = item.get("source_locators")
            if not isinstance(locators, list) or not locators or not all(has_locator(x) for x in locators):
                issues.append(f"{prefix} not_present override needs source_locators")
        # Even optional quotes on a not_present item must not contain invented evidence.
        if status == "not_present":
            for field, original in (("source_quotes", source_text), ("draft_quotes", draft_text)):
                if field in item and item[field] != [] and not list_has_evidence(original, item[field]):
                    issues.append(f"{prefix} {field} must contain only real excerpts")
        if status == "checked":
            source_locators = item.get("source_locators")
            draft_locators = item.get("draft_locators")
            preserved = item.get("preserved_items")
            source_quotes = item.get("source_quotes")
            draft_quotes = item.get("draft_quotes")
            if not isinstance(source_locators, list) or not any(has_locator(x) for x in source_locators):
                issues.append(f"{prefix} checked item needs source_locators")
            if not isinstance(draft_locators, list) or not any(has_locator(x) for x in draft_locators):
                issues.append(f"{prefix} checked item needs draft_locators")
            if not isinstance(preserved, list) or not any(isinstance(x, str) and x.strip() for x in preserved):
                issues.append(f"{prefix} checked item needs preserved_items")
            if not list_has_evidence(source_text, source_quotes):
                issues.append(f"{prefix} checked item needs real source_quotes")
            elif not list_has_risk_signal(kind, source_quotes, speaker_map_sha256) and not semantic_reason:
                issues.append(f"{prefix} source_quotes do not evidence {kind}; a concrete semantic_reason is required for a lexical miss")
            if not list_has_evidence(draft_text, draft_quotes):
                issues.append(f"{prefix} checked item needs real draft_quotes")
            elif not list_has_risk_signal(kind, draft_quotes, speaker_map_sha256) and not semantic_reason:
                issues.append(f"{prefix} draft_quotes do not evidence {kind}; a concrete semantic_reason is required for a lexical miss")
        for side, original in (("source", source_text), ("draft", draft_text)):
            locator_key, quote_key = f"{side}_locators", f"{side}_quotes"
            if status == "checked" or locator_key in item or quote_key in item:
                validate_locator_list(
                    original, item.get(locator_key, []), item.get(quote_key, []),
                    f"{prefix} {side}", issues, locator_counts,
                )
        unresolved = item.get("unresolved_items", [])
        if not isinstance(unresolved, list):
            issues.append(f"{prefix} unresolved_items must be a list")
        elif unresolved:
            issues.append(f"{prefix} has unresolved items")
        blocking = item.get("blocking_issues", [])
        if not isinstance(blocking, list):
            issues.append(f"{prefix} blocking_issues must be a list")
        elif blocking:
            issues.append(f"{prefix} has blocking issues")
    missing_risks = sorted(REQUIRED_RISK_CHECKS - seen_risks)
    if missing_risks:
        issues.append(f"review report missing risk checks: {missing_risks}")
    if duplicated_risks:
        issues.append(f"review report has duplicate risk checks: {sorted(duplicated_risks)}")
    if review_report.get("result") != "pass":
        issues.append("review report result must be pass")
    stats.update(
        {
            "review_sample_kinds": sorted(seen_samples),
            "review_risk_kinds": sorted(seen_risks),
            "review_result": review_report.get("result"),
            "review_locator_counts": locator_counts,
            "review_locator_verification": (
                "invalid" if locator_counts["invalid"]
                else "legacy_free_text" if locator_counts["legacy_free_text"]
                else "structured_line_ranges"
            ),
            "legacy_locator_ranges_verified": False if locator_counts["legacy_free_text"] else None,
            "detected_risk_kinds": sorted(
                kind
                for kind in REQUIRED_RISK_CHECKS
                if risk_signal_present(kind, source_text, speaker_map_sha256)
            ),
        }
    )
    return issues, stats


def review_evidence_snapshot(review_report):
    if not isinstance(review_report, dict):
        return None
    return {
        "version": review_report.get("version"),
        "mode": review_report.get("mode"),
        "samples": review_report.get("samples"),
        "risk_checks": review_report.get("risk_checks"),
        "result": review_report.get("result"),
    }


ANALYZERS = {"exchange": analyze_exchange, "review": analyze_review, "learning": analyze_learning}


def analyze(
    mode,
    draft_text,
    source_text=None,
    expected_duration=None,
    speaker_map=None,
    people_count=None,
    asr_channel_count=None,
    speaker_state="stable",
    final_check=False,
    review_report=None,
    speaker_map_sha256=None,
    require_exchange_metadata=False,
):
    if mode == "exchange":
        issues, warnings, stats = analyze_exchange(
            draft_text, source_text, expected_duration, require_metadata=require_exchange_metadata
        )
    else:
        issues, warnings, stats = ANALYZERS[mode](draft_text, source_text, expected_duration)
    if mode == "exchange":
        map_issues, map_stats = validate_speaker_map(
            draft_text,
            speaker_map,
            people_count,
            asr_channel_count,
            speaker_state=speaker_state,
            final_check=final_check,
        )
        issues.extend(map_issues)
        stats.update(map_stats)
    structural_ok = not issues
    final_issues, review_stats = [], {}
    if final_check:
        if source_text is None:
            final_issues.append("final check requires source text")
        review_issues, review_stats = validate_review_report(
            review_report,
            mode,
            source_text,
            draft_text,
            speaker_map_sha256=speaker_map_sha256,
        )
        final_issues.extend(review_issues)
    final_ready = final_check and structural_ok and not final_issues
    if final_ready:
        final_ready_reason = "review_report_traceability_complete"
        review_status = "complete"
    elif not final_check:
        final_ready_reason = "final_check_not_requested"
        review_status = "not_requested"
    elif not structural_ok:
        final_ready_reason = "structural_or_traceability_issues"
        review_status = "blocked"
    else:
        final_ready_reason = "review_report_incomplete_or_mismatched"
        review_status = "failed"
    stats.update(review_stats)
    stats.setdefault("source_chars", len(source_text) if source_text is not None else None)
    report = {
        "validation_report_contract": VALIDATION_REPORT_CONTRACT,
        "ok": structural_ok,
        "structural_ok": structural_ok,
        "final_ready": final_ready,
        "final_ready_reason": final_ready_reason,
        "mode": mode,
        "validation_scope": "structure_and_traceability",
        "semantic_review_required": not final_ready,
        "program_semantics_verified": False,
        "review_completion_description": (
            "结构与证据检查通过，模型已提交复核结论；程序未验证语义正确。"
            if final_ready else "复核尚未通过；程序检查范围为结构与证据，不代替内容判断。"
        ),
        "source_aligned_review_status": review_status,
        "issues": issues,
        "final_issues": final_issues,
        "warnings": warnings,
        "stats": stats,
    }
    evidence = review_evidence_snapshot(review_report) if final_check else None
    if evidence is not None:
        report["review_evidence"] = evidence
        report["review_evidence_sha256"] = json_sha256(evidence)
    return report


def main(argv=None, forced_mode=None):
    parser = argparse.ArgumentParser(description="Validate transcript-refine artifacts")
    if forced_mode is None:
        parser.add_argument("--mode", required=True, choices=tuple(ANALYZERS))
    parser.add_argument("--draft", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-duration-seconds", type=int)
    parser.add_argument("--speaker-map")
    parser.add_argument("--speaker-state", required=True, choices=("stable", "unstable"))
    parser.add_argument("--people-count", type=int)
    parser.add_argument("--asr-channel-count", type=int)
    parser.add_argument("--final-check", action="store_true")
    parser.add_argument("--review-report")
    parser.add_argument("--report-out", help="把完整 JSON 校验结果写入文件，供保存门禁使用")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    if forced_mode is not None:
        args.mode = forced_mode
    if args.review_report and not args.final_check:
        parser.error("--review-report requires --final-check")
    draft_text = read_text_file(args.draft)
    source_text = read_text_file(args.source)
    speaker_map_text = read_text_file(args.speaker_map) if args.speaker_map else None
    speaker_map = json.loads(speaker_map_text) if speaker_map_text else None
    review_report_text = read_text_file(args.review_report) if args.review_report else None
    review_report = json.loads(review_report_text) if review_report_text else None
    report = analyze(
        args.mode,
        draft_text,
        source_text,
        args.expected_duration_seconds,
        speaker_map,
        args.people_count,
        args.asr_channel_count,
        speaker_state=args.speaker_state,
        final_check=args.final_check,
        review_report=review_report,
        speaker_map_sha256=text_sha256(speaker_map_text) if speaker_map_text else None,
    )
    report["draft_sha256"] = text_sha256(draft_text)
    report["source_sha256"] = text_sha256(source_text)
    if speaker_map_text:
        report["speaker_map_sha256"] = text_sha256(speaker_map_text)
    if review_report_text:
        report["review_report_sha256"] = text_sha256(review_report_text)
    rendered_report = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report_out:
        Path(args.report_out).write_text(rendered_report + "\n", encoding="utf-8")
    if args.json:
        print(rendered_report)
    else:
        print(
            f"ok={report['ok']} mode={report['mode']} "
            f"scope={report['validation_scope']} final_ready={str(report['final_ready']).lower()}"
        )
        for item in report["issues"]:
            print(f"ERROR: {item}")
        for item in report["final_issues"]:
            print(f"FINAL ERROR: {item}")
        for item in report["warnings"]:
            print(f"WARN: {item}")
    if not report["ok"]:
        return 1
    if args.final_check and not report["final_ready"]:
        return 1
    if args.strict and report["warnings"]:
        return 2
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
