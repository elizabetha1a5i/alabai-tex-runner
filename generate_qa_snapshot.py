"""
generate_qa_snapshot.py
Reads tex_prod_results.csv from the current run and appends a new entry
to reports/qa_results.json in the cyphr-flow checkout directory.

Usage (in CI):
    python generate_qa_snapshot.py --csv tex_prod_results.csv --out /tmp/cyphr-flow/reports/qa_results.json
"""
import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path


def load_csv(csv_path):
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def build_run_entry(rows, label=None):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    label = label or datetime.utcnow().strftime("%B %Y")

    total    = len(rows)
    statuses = [r.get("status", "").upper() for r in rows]
    passed   = sum(1 for s in statuses if s == "PASS")
    failed   = sum(1 for s in statuses if s == "FAIL")
    warned   = sum(1 for s in statuses if s == "WARN")
    errored  = sum(1 for s in statuses if s == "ERROR")
    # Treat WARN as partial (passed overall but flagged)
    partial  = warned

    pass_rate = round((passed + partial) / total * 100, 1) if total else 0

    # Per-category breakdown
    categories = {}
    for row in rows:
        cat    = row.get("category", "Unknown")
        status = row.get("status", "").upper()
        if cat not in categories:
            categories[cat] = {"total": 0, "pass": 0, "partial": 0, "fail": 0, "error": 0}
        categories[cat]["total"] += 1
        if status == "PASS":
            categories[cat]["pass"] += 1
        elif status == "WARN":
            categories[cat]["partial"] += 1
        elif status == "FAIL":
            categories[cat]["fail"] += 1
        else:
            categories[cat]["error"] += 1

    # Per-test detail
    tests = []
    for row in rows:
        status = row.get("status", "").upper()
        tests.append({
            "id":       row.get("test_id", ""),
            "name":     row.get("name", ""),
            "category": row.get("category", ""),
            "status":   status,
            "score":    row.get("score", ""),
            "critical_failures": row.get("critical_failures", ""),
            "high_failures":     row.get("high_failures", ""),
            "notes":    row.get("notes", ""),
            "summary":  row.get("summary", ""),
        })

    # Partials = WARN tests (passed overall but have flagged criteria)
    partials = [t for t in tests if t["status"] == "WARN"]

    return {
        "date":      today,
        "label":     label,
        "total":     total,
        "pass":      passed,
        "partial":   partial,
        "fail":      failed,
        "error":     errored,
        "pass_rate": pass_rate,
        "categories": categories,
        "tests":     tests,
        "partials":  partials,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",   default="tex_prod_results.csv", help="Path to results CSV")
    parser.add_argument("--out",   required=True,                  help="Path to qa_results.json (will create/append)")
    parser.add_argument("--label", default=None,                   help="Run label e.g. 'June 2026'")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"[ERROR] CSV not found: {args.csv}")
        raise SystemExit(1)

    rows      = load_csv(args.csv)
    run_entry = build_run_entry(rows, args.label)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing history or start fresh
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {"runs": []}
    else:
        existing = {"runs": []}

    # Remove any entry with the same date (re-run on same day replaces)
    existing["runs"] = [r for r in existing["runs"] if r.get("date") != run_entry["date"]]
    existing["runs"].append(run_entry)

    # Keep last 30 runs max
    existing["runs"] = existing["runs"][-30:]

    out_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Snapshot written: {out_path}")
    print(f"     Run: {run_entry['date']} | {run_entry['total']} tests | {run_entry['pass_rate']}% pass rate")


if __name__ == "__main__":
    main()
