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


def build_run_entry(rows, label=None, screenshots_dir=None, run_date=None):
    now   = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    label = label or now.strftime("%B %Y")

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
        test_id = row.get("test_id", "")
        # Check if a local screenshot was copied to the repo for this test
        screenshot_local = ""
        if screenshots_dir and run_date and status in ("FAIL", "WARN"):
            local_png = Path(screenshots_dir) / f"{test_id}.png"
            if local_png.exists():
                screenshot_local = f"screenshots/{run_date}/{test_id}.png"
        tests.append({
            "id":                test_id,
            "test_case_id":      test_id,
            "test_case_version": row.get("test_case_version", ""),
            "name":              row.get("name", ""),
            "category":          row.get("category", ""),
            "status":            status,
            "score":             row.get("score", ""),
            "critical_failures": row.get("critical_failures", ""),
            "high_failures":     row.get("high_failures", ""),
            "criteria_failed":   row.get("criteria_failed", "") or row.get("all_failed_criteria", ""),
            "notes":             row.get("notes", ""),
            "summary":           row.get("summary", ""),
            "screenshot_path":   row.get("screenshot_path", ""),
            "screenshot_local":  screenshot_local,
            "conversation_path": row.get("conversation_path", ""),
        })

    # Partials = WARN tests (passed overall but have flagged criteria)
    partials = [t for t in tests if t["status"] == "WARN"]

    return {
        "date":      today,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    parser.add_argument("--csv",              default="tex_prod_results.csv", help="Path to results CSV")
    parser.add_argument("--out",              required=True,                  help="Path to qa_results.json (will create/append)")
    parser.add_argument("--label",            default=None,                   help="Run label e.g. 'June 2026'")
    parser.add_argument("--environment",      default="production",           help="Environment: staging | production")
    parser.add_argument("--test-type",        default="criteria",             help="Test type: criteria | custom | feedback")
    parser.add_argument("--screenshots-dir",  default=None,                   help="Dir where fail/partial PNGs were copied, e.g. cyphr-flow/screenshots/2026-06-18")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"[ERROR] CSV not found: {args.csv}")
        raise SystemExit(1)

    run_date  = datetime.utcnow().strftime("%Y-%m-%d")
    rows      = load_csv(args.csv)
    run_entry = build_run_entry(rows, args.label, args.screenshots_dir, run_date)
    run_entry["environment"] = args.environment
    run_entry["test_type"]   = getattr(args, "test_type")
    run_entry["run_id"]      = f"{run_entry['timestamp']}-{args.environment}-{getattr(args, 'test_type')}"

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

    # Remove any entry with the same run_id (exact re-run replaces; different times coexist)
    existing["runs"] = [
        r for r in existing["runs"]
        if r.get("run_id") != run_entry["run_id"]
    ]
    existing["runs"].append(run_entry)

    # Keep last 30 runs live in qa_results.json (what the dashboard fetches),
    # but archive older runs to reports/history/ instead of dropping them —
    # full execution history is retained indefinitely for audit purposes.
    if len(existing["runs"]) > 30:
        overflow = existing["runs"][:-30]
        history_dir = out_path.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        for old_run in overflow:
            archive_path = history_dir / f"{old_run['run_id']}.json"
            if not archive_path.exists():
                archive_path.write_text(json.dumps(old_run, indent=2, ensure_ascii=False), encoding="utf-8")
        existing["runs"] = existing["runs"][-30:]

    out_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Snapshot written: {out_path}")
    print(f"     Run: {run_entry['run_id']} | {run_entry['total']} tests | {run_entry['pass_rate']}% pass rate")


if __name__ == "__main__":
    main()
