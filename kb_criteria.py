"""
kb_criteria.py
──────────────
Loads kb/qa_criteria.csv — the single, hand-editable table of QA rules and
brand facts with fixed severity/weight columns. Open the CSV in Excel,
Google Sheets, or VS Code to add/adjust rows directly; no sync step, no
Google auth required. This is the source of truth prompt_loader.py and
dynamic_evaluator.py build from.
"""
import csv
import os

CSV_PATH = os.path.join(os.path.dirname(__file__), "kb", "qa_criteria.csv")

_cache = None


def load_criteria():
    """Returns a list of dicts, one per CSV row:
    {id, type, categories (list[str]), severity, weight (int), text, source}
    Cached after first read — delete the module-level cache (_cache = None)
    or restart the process to pick up mid-run CSV edits."""
    global _cache
    if _cache is not None:
        return _cache

    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({
                "id": row["id"],
                "type": row["type"],
                "categories": [c.strip() for c in row["categories"].split(",")],
                "severity": row["severity"],
                "weight": int(row["weight"]),
                "text": row["text"],
                "source": row["source"],
            })
    _cache = rows
    return rows


def rules_for_category(category):
    """Rules (not facts) that apply to ALL categories or the given one."""
    return [
        r for r in load_criteria()
        if r["type"] == "rule" and ("ALL" in r["categories"] or category in r["categories"])
    ]


def facts():
    """Brand facts (type=fact) rows."""
    return [r for r in load_criteria() if r["type"] == "fact"]


def find_matching(text_fragment):
    """Best-effort lookup: find a criteria row whose id or text loosely
    matches the given fragment (a Gemini-produced issue theme/label/note).
    Used to attach a fixed weight to a dynamically-flagged issue."""
    fragment = text_fragment.lower()
    for r in load_criteria():
        if r["id"].lower() in fragment or fragment in r["id"].lower():
            return r
    for r in load_criteria():
        if r["text"].lower() in fragment or fragment in r["text"].lower():
            return r
    return None
