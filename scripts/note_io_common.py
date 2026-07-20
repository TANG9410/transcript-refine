#!/usr/bin/env python3
"""Get笔记保存脚本共用的确定性 read-back 校验。"""

import hashlib


def normalize_text(text):
    """统一换行，只忽略正文末尾一个换行。"""
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return value[:-1] if value.endswith("\n") else value


def text_sha256(text):
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def verify_readback(note, expected_title=None, expected_content=None):
    actual_title = note.get("title")
    actual_content = note.get("content", "")
    title_verified = expected_title is None or actual_title == expected_title
    content_verified = (
        expected_content is None
        or normalize_text(actual_content) == normalize_text(expected_content)
    )
    return {
        "title_verified": title_verified,
        "content_verified": content_verified,
        "content_chars": len(normalize_text(actual_content)),
        "content_sha256": text_sha256(actual_content),
        "read_back": "verified" if title_verified and content_verified else "mismatch",
    }
