#!/usr/bin/env python3

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
KNOWLEDGE_FILE = ROOT / "knowledge_cutoff_reply.md"
KHAMENEI_FILE = ROOT / "Khamenei_test.md"
SUMMARY_CSV = ROOT / "khamenei_summary.csv"
NON_NETWORK_CSV = ROOT / "khamenei_non_network_sorted.csv"


SECTION_RE = re.compile(
    r"^##\s+\d+\.\s+(.+?)\n\n- Status: `([^`]+)`.*?\nReply:\n\n`````text\n(.*?)\n`````",
    re.M | re.S,
)


MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


@dataclass
class Section:
    model: str
    status: str
    reply: str


@dataclass
class CutoffInfo:
    normalized: str
    sort_date: date | None


def parse_sections(path: Path) -> dict[str, Section]:
    text = path.read_text(encoding="utf-8")
    sections: dict[str, Section] = {}
    for model, status, reply in SECTION_RE.findall(text):
        sections[model.strip()] = Section(
            model=model.strip(),
            status=status.strip(),
            reply=reply.strip(),
        )
    return sections


def extract_cutoff(reply: str, status: str) -> CutoffInfo:
    if status != "ok":
        return CutoffInfo("N.A", None)

    text = " ".join(reply.replace("\r", "").split())
    lower = text.lower()

    realtime_markers = [
        "没有固定截止日期",
        "没有严格的截止日期",
        "没有固定的截止日期",
        "持续更新",
        "实时工具",
        "实时更新",
        "can access up-to-date information",
        "continuously updated",
        "no fixed cutoff",
        "up to current",
    ]
    if any(marker in text for marker in realtime_markers) or any(marker in lower for marker in realtime_markers):
        return CutoffInfo("实时更新", None)

    for pattern, fmt in [
        (r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", "ymd"),
        (r"\b(20\d{2})[-/.](\d{1,2})\b", "ym"),
        (r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", "ymd"),
        (r"(20\d{2})\s*年\s*(\d{1,2})\s*月", "ym"),
        (r"\b(20\d{2})\b", "y"),
    ]:
        match = re.search(pattern, text, re.I)
        if not match:
            continue

        year = int(match.group(1))
        month = 1
        day = 1
        if fmt in {"ym", "ymd"}:
            month = int(match.group(2))
        if fmt == "ymd":
            day = int(match.group(3))

        try:
            parsed = date(year, month, day)
        except ValueError:
            continue

        if fmt == "ymd":
            return CutoffInfo(f"{year:04d}-{month:02d}-{day:02d}", parsed)
        if fmt == "ym":
            return CutoffInfo(f"{year:04d}-{month:02d}", parsed)
        return CutoffInfo(f"{year:04d}", parsed)

    month_name_patterns = [
        r"(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sep|sept|october|oct|november|nov|december|dec)\s+(\d{1,2}),?\s+(20\d{2})",
        r"(20\d{2})\s+(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sep|sept|october|oct|november|nov|december|dec)",
        r"(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sep|sept|october|oct|november|nov|december|dec)\s+(20\d{2})",
    ]

    for idx, pattern in enumerate(month_name_patterns):
        match = re.search(pattern, lower, re.I)
        if not match:
            continue

        if idx == 0:
            month = MONTHS[match.group(1).lower()]
            day = int(match.group(2))
            year = int(match.group(3))
            try:
                parsed = date(year, month, day)
            except ValueError:
                continue
            return CutoffInfo(f"{year:04d}-{month:02d}-{day:02d}", parsed)

        if idx == 1:
            year = int(match.group(1))
            month = MONTHS[match.group(2).lower()]
        else:
            month = MONTHS[match.group(1).lower()]
            year = int(match.group(2))

        parsed = date(year, month, 1)
        return CutoffInfo(f"{year:04d}-{month:02d}", parsed)

    return CutoffInfo("N.A", None)


def classify_network(reply: str, status: str) -> str:
    if status != "ok":
        return "N.A"

    text = reply.lower()
    patterns = [
        r"2026[-/.]0?2[-/.]28",
        r"2026[-/.]0?3[-/.]0?1",
        r"2月\s*28日",
        r"3月\s*1日",
        r"feb(?:ruary)?\s+28(?:,?\s+2026)?",
        r"march\s+1(?:st)?(?:,?\s+2026)?",
        r"mar\s+1(?:st)?(?:,?\s+2026)?",
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.I):
            return "能联网"
    return "不能联网"


def sort_key(row: dict[str, str]) -> tuple[int, date, str]:
    if row["knowledge_cutoff_normalized"] == "实时更新":
        return (1, date.max, row["model"])
    if row["knowledge_cutoff_normalized"] == "N.A":
        return (2, date.max, row["model"])

    normalized = row["knowledge_cutoff_normalized"]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        parsed = date.fromisoformat(normalized)
    elif re.fullmatch(r"\d{4}-\d{2}", normalized):
        parsed = date(int(normalized[:4]), int(normalized[5:7]), 1)
    elif re.fullmatch(r"\d{4}", normalized):
        parsed = date(int(normalized), 1, 1)
    else:
        parsed = date.max
    return (0, parsed, row["model"])


def main() -> None:
    knowledge_sections = parse_sections(KNOWLEDGE_FILE)
    khamenei_sections = parse_sections(KHAMENEI_FILE)

    all_models = sorted(set(knowledge_sections) | set(khamenei_sections))
    summary_rows: list[dict[str, str]] = []

    for model in all_models:
        knowledge = knowledge_sections.get(model, Section(model, "N.A", ""))
        khamenei = khamenei_sections.get(model, Section(model, "N.A", ""))
        cutoff = extract_cutoff(knowledge.reply, knowledge.status)
        network = classify_network(khamenei.reply, khamenei.status)

        summary_rows.append(
            {
                "model": model,
                "knowledge_cutoff_normalized": cutoff.normalized,
                "knowledge_cutoff_status": knowledge.status,
                "knowledge_cutoff_raw_reply": knowledge.reply,
                "network_status": network,
                "khamenei_test_status": khamenei.status,
                "khamenei_test_raw_reply": khamenei.reply,
            }
        )

    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "model",
                "knowledge_cutoff_normalized",
                "knowledge_cutoff_status",
                "knowledge_cutoff_raw_reply",
                "network_status",
                "khamenei_test_status",
                "khamenei_test_raw_reply",
            ],
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    non_network_rows = [
        {
            "model": row["model"],
            "knowledge_cutoff_normalized": row["knowledge_cutoff_normalized"],
            "knowledge_cutoff_status": row["knowledge_cutoff_status"],
            "knowledge_cutoff_raw_reply": row["knowledge_cutoff_raw_reply"],
            "network_status": row["network_status"],
        }
        for row in summary_rows
        if row["network_status"] == "不能联网"
    ]
    non_network_rows.sort(key=sort_key)

    with NON_NETWORK_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "model",
                "knowledge_cutoff_normalized",
                "knowledge_cutoff_status",
                "knowledge_cutoff_raw_reply",
                "network_status",
            ],
        )
        writer.writeheader()
        writer.writerows(non_network_rows)


if __name__ == "__main__":
    main()
