#!/usr/bin/env python3
"""三种逐字稿主稿的确定性校验器。"""

import argparse
import json
import re
import sys
from pathlib import Path


TIME = r"\d{2}:\d{2}(?::\d{2})?"
TOPIC_RE = re.compile(rf"^##\s+(.+?)\s+[\[（(]({TIME})\s*[-~～—–]\s*({TIME})[\]）)]\s*$")
SPEAKER_RE = re.compile(r"^\*\*(.+?)[：:]\*\*")
TIME_TOKEN_RE = re.compile(rf"\[{TIME}\]")
STANDALONE_TIME_RE = re.compile(rf"^\s*\*{{0,2}}\[{TIME}\]\*{{0,2}}\s*$")
ANCHOR_RE = re.compile(r"【S\d{2,}(?:｜[^】]+)+】")
SOURCE_ID_RE = re.compile(r"\bS\d{2,}\b")
LOW_CONFIDENCE_RE = re.compile(r"^(?:说话人[A-Z0-9]+|低置信(?:说话人)?[A-Z0-9]+)$")
FILLERS = ("嗯嗯嗯", "对对对", "我我我", "这个这个这个", "就是就是就是")
ASR_NOISE = ("G U", "G E U", "指定发生单位", "SARS", "萨克斯", "董成弟")
EXCHANGE_APPENDIX_RE = re.compile(
    r"^##\s+(?:附录(?:\b|[：:])|(?:📊\s*)?(?:销售复盘观察|销售观察|复盘观察)|"
    r"时间覆盖检查|人物关系(?:与低置信说明)?|疑似识别错误与修正说明|精炼说明)"
)


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


def retention_warning(draft_text, source_text):
    if not source_text or len(source_text) < 10000:
        return None, None
    ratio = len(draft_text) / len(source_text)
    if ratio < 0.35:
        return ratio, f"retention ratio {ratio:.1%}: possible over-compression"
    if ratio > 0.80:
        return ratio, f"retention ratio {ratio:.1%}: possible near-verbatim搬运"
    return ratio, None


def validate_speaker_map(draft_text, speaker_map, people_count=None, asr_channel_count=None):
    issues = []
    stats = {"speaker_map_required": bool(people_count and asr_channel_count and people_count > asr_channel_count)}
    if stats["speaker_map_required"] and speaker_map is None:
        return ["complex multi-person task requires --speaker-map"], stats
    if speaker_map is None:
        return issues, stats
    if not isinstance(speaker_map, dict):
        return ["speaker map must be a JSON object"], stats
    if speaker_map.get("version") != 1:
        issues.append("speaker map version must be 1")
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
    output_labels = set()
    unresolved_candidates = set()
    unresolved_count = 0
    locked_count = 0
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
        if not isinstance(output_label, str) or not output_label.strip():
            issues.append(f"{prefix} missing output_label")
            continue
        output_labels.add(output_label.strip())
        if not isinstance(evidence, list) or not any(isinstance(x, str) and x.strip() for x in evidence):
            issues.append(f"{prefix} needs non-empty evidence")
        if confidence not in {"high", "medium", "low", "unknown"}:
            issues.append(f"{prefix} has invalid confidence")
        if status == "locked":
            locked_count += 1
            if not isinstance(person, str) or not person.strip():
                issues.append(f"{prefix} locked mapping requires person")
            elif output_label.strip() != person.strip():
                issues.append(f"{prefix} locked output_label must equal person")
            if confidence not in {"high", "medium"}:
                issues.append(f"{prefix} locked mapping needs high or medium confidence")
        elif status == "unresolved":
            unresolved_count += 1
            if person not in (None, ""):
                issues.append(f"{prefix} unresolved mapping must not set person")
            if not LOW_CONFIDENCE_RE.fullmatch(output_label.strip()):
                issues.append(f"{prefix} unresolved mapping must use a low-confidence label")
            if confidence not in {"low", "unknown"}:
                issues.append(f"{prefix} unresolved mapping needs low or unknown confidence")
            candidates = item.get("candidates", [])
            if not isinstance(candidates, list):
                issues.append(f"{prefix} candidates must be a list")
            else:
                unresolved_candidates.update(x.strip() for x in candidates if isinstance(x, str) and x.strip())
        else:
            issues.append(f"{prefix} status must be locked or unresolved")

    draft_labels = set()
    for line in draft_text.splitlines():
        match = SPEAKER_RE.match(line.strip())
        if match:
            draft_labels.add(match.group(1).strip())
    unknown_labels = sorted(draft_labels - output_labels)
    if unknown_labels:
        issues.append(f"draft speaker labels missing from speaker map: {unknown_labels}")
    unknown_generic = sorted(label for label in draft_labels if LOW_CONFIDENCE_RE.fullmatch(label) and label not in output_labels)
    if unknown_generic:
        issues.append(f"draft low-confidence labels missing from speaker map: {unknown_generic}")
    guessed = sorted(draft_labels & unresolved_candidates)
    if guessed:
        issues.append(f"unresolved people used as named speakers: {guessed}")
    stats.update({"speaker_map_present": True, "speaker_map_locked": locked_count, "speaker_map_unresolved": unresolved_count})
    return issues, stats


def analyze_exchange(draft_text, source_text=None, expected_duration=None):
    issues, warnings, body_lines, topics = [], [], [], []
    for line in draft_text.splitlines():
        if EXCHANGE_APPENDIX_RE.match(line.strip()):
            break
        body_lines.append(line)
    body_text = "\n".join(body_lines)
    for line_no, line in enumerate(body_lines, start=1):
        if not line.startswith("## "):
            continue
        match = TOPIC_RE.match(line)
        if not match:
            issues.append(f"line {line_no}: topic heading missing valid time range")
            continue
        try:
            start, end = parse_time(match.group(2)), parse_time(match.group(3))
        except ValueError:
            issues.append(f"line {line_no}: invalid minute or second")
            continue
        if end < start:
            issues.append(f"line {line_no}: topic end precedes start")
        topics.append((line_no, start, end))
    if not topics:
        issues.append("no timed topic headings found")
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
    ratio, ratio_warning = retention_warning(body_text, source_text)
    if ratio_warning:
        warnings.append(ratio_warning)
    return issues, warnings, {"topic_count": len(topics), "retention_ratio": ratio}


def review_rows(section_text):
    if not section_text:
        return []
    return [line.strip() for line in section_text.splitlines() if line.strip().startswith("- ") and "无" not in line.strip()]


def analyze_review(draft_text, source_text=None, expected_duration=None):
    del source_text, expected_duration
    issues, warnings = [], []
    headings = ("背景与目标", "已确认事实", "主要判断与分歧", "风险与待确认", "行动项", "来源覆盖")
    sections = {name: section(draft_text, name) for name in headings}
    for name, content in sections.items():
        if content is None:
            issues.append(f"missing required section: {name}")
    for name in ("主要判断与分歧", "风险与待确认"):
        for line in review_rows(sections.get(name)):
            if not ANCHOR_RE.search(line):
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
        if len(cells) >= 5 and not ANCHOR_RE.search(cells[-1]):
            issues.append(f"action item missing evidence anchor: {stripped[:80]}")
    coverage = sections.get("来源覆盖") or ""
    coverage_ids = set(SOURCE_ID_RE.findall(coverage))
    if coverage and not coverage_ids:
        issues.append("source coverage contains no Sxx ids")
    anchors = ANCHOR_RE.findall(draft_text)
    if not anchors:
        issues.append("no evidence anchors found")
    return issues, warnings, {"evidence_anchor_count": len(anchors), "coverage_source_ids": len(coverage_ids)}


def analyze_learning(draft_text, source_text=None, expected_duration=None):
    del expected_duration
    issues, warnings = [], []
    required = ("基本信息", "课程地图", "源内容覆盖表", "待确认与 ASR 修正")
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
        if not stripped.startswith("|") or "源段" in stripped or re.match(r"^\|?\s*[-:]+", stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 4 and SOURCE_ID_RE.fullmatch(cells[0]):
            rows.append(cells)
            if cells[2] not in {"详细保留", "压缩交代", "删除"}:
                issues.append(f"invalid coverage action for {cells[0]}: {cells[2]}")
            if cells[2] == "删除" and len(cells[3]) < 2:
                issues.append(f"deleted source segment lacks reason: {cells[0]}")
    if not rows:
        issues.append("source coverage table has no valid Sxx rows")
    missing = sorted(set(SOURCE_ID_RE.findall(source_text or "")) - {row[0] for row in rows})
    if missing:
        issues.append(f"source ids missing from coverage table: {missing}")
    signals = {"案例": ("案例", "举例", "比如"), "数据": ("数据", "%", "万", "元"), "限制": ("限制", "边界", "前提", "条件", "不能"), "问答": ("问答", "Q&A", "提问", "问题")}
    for label, terms in signals.items():
        if source_text and any(term in source_text for term in terms) and not any(term in draft_text for term in terms):
            warnings.append(f"source contains {label} signals but draft does not")
    promo_terms = ("门票", "会员促销", "下单", "报名", "老板节", "粉丝灯牌")
    if sum((source_text or "").count(term) for term in promo_terms) >= 3 and not ("删除" in coverage and "促销" in coverage):
        warnings.append("source has repeated promotion signals but coverage table lacks a promotion deletion reason")
    ratio, ratio_warning = retention_warning(draft_text, source_text)
    if ratio_warning:
        warnings.append(ratio_warning)
    return issues, warnings, {"knowledge_chapter_count": len(chapters), "coverage_rows": len(rows), "retention_ratio": ratio}


ANALYZERS = {"exchange": analyze_exchange, "review": analyze_review, "learning": analyze_learning}


def analyze(mode, draft_text, source_text=None, expected_duration=None, speaker_map=None, people_count=None, asr_channel_count=None):
    issues, warnings, stats = ANALYZERS[mode](draft_text, source_text, expected_duration)
    if mode == "exchange":
        map_issues, map_stats = validate_speaker_map(draft_text, speaker_map, people_count, asr_channel_count)
        issues.extend(map_issues)
        stats.update(map_stats)
    return {"ok": not issues, "mode": mode, "issues": issues, "warnings": warnings, "stats": stats}


def main(argv=None, forced_mode=None):
    parser = argparse.ArgumentParser(description="Validate transcript-refine-v3 artifacts")
    if forced_mode is None:
        parser.add_argument("--mode", required=True, choices=tuple(ANALYZERS))
    parser.add_argument("--draft", required=True)
    parser.add_argument("--source")
    parser.add_argument("--expected-duration-seconds", type=int)
    parser.add_argument("--speaker-map")
    parser.add_argument("--people-count", type=int)
    parser.add_argument("--asr-channel-count", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    if forced_mode is not None:
        args.mode = forced_mode
    draft_text = Path(args.draft).read_text(encoding="utf-8")
    source_text = Path(args.source).read_text(encoding="utf-8") if args.source else None
    speaker_map = json.loads(Path(args.speaker_map).read_text(encoding="utf-8")) if args.speaker_map else None
    report = analyze(args.mode, draft_text, source_text, args.expected_duration_seconds, speaker_map, args.people_count, args.asr_channel_count)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ok={report['ok']} mode={report['mode']}")
        for item in report["issues"]:
            print(f"ERROR: {item}")
        for item in report["warnings"]:
            print(f"WARN: {item}")
    if not report["ok"]:
        return 1
    if args.strict and report["warnings"]:
        return 2
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
