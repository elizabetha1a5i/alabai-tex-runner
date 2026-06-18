"""
copy_screenshots.py
Copies fail/partial screenshots from /tmp/ into the cyphr-flow repo.
Run in CI after the test runner, before the snapshot generator.

Usage:
    python copy_screenshots.py --csv tex_prod_results.csv --dest cyphr-flow/screenshots/2026-06-18
"""
import argparse
import csv
import os
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",  required=True, help="Path to results CSV")
    parser.add_argument("--dest", required=True, help="Destination folder, e.g. cyphr-flow/screenshots/2026-06-18")
    args = parser.parse_args()

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    if not os.path.exists(args.csv):
        print(f"[SKIP] CSV not found: {args.csv}")
        return

    copied = 0
    with open(args.csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("status", "").upper() not in ("FAIL", "WARN"):
                continue
            tid = row.get("test_id", "")
            for suffix in ("_final.png", "_partial.png"):
                src = Path(f"/tmp/{tid}{suffix}")
                if src.exists():
                    shutil.copy(src, dest / f"{tid}.png")
                    copied += 1
                    break

    print(f"[OK] {copied} screenshot(s) copied to {dest}")


if __name__ == "__main__":
    main()
