"""
Shared loader/writer for qa/test_cases.json — the versioned, structured
test-case store that replaced the hardcoded TEST_SCRIPTS list.

Schema per entry:
    id, title, category, preconditions, conversation, expected_result,
    owner, status (draft|in_review|approved|active|deprecated),
    version, requirement_ref, created_at, updated_at, history
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STORE_PATH = Path(__file__).parent / "test_cases.json"

RUNNABLE_STATUSES = {"active", "approved"}


def _dashboard_url():
    return os.environ.get("EVAL_DASHBOARD_URL", "").rstrip("/")


def _fetch_from_dashboard(statuses):
    url = f"{_dashboard_url()}/api/test-cases?status={','.join(statuses)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.load(resp)["test_cases"]


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_all(path=STORE_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["test_cases"]


def load_runnable(path=STORE_PATH, statuses=RUNNABLE_STATUSES):
    """Test cases eligible to be picked up by a scheduled/CI run.

    Prefers the Tex-eval-dashboard Postgres store (source of truth) when
    EVAL_DASHBOARD_URL is set, e.g. in CI. Falls back to the local JSON file
    for offline/local development.
    """
    if _dashboard_url():
        try:
            return _fetch_from_dashboard(statuses)
        except Exception as e:
            print(f"⚠️  Could not reach dashboard test-case API ({e}); falling back to local {path}")

    return [t for t in load_all(path) if t["status"] in statuses]


def as_test_script(test_case):
    """Adapt a stored test case back into the {test_id, name, category,
    description, conversation} shape the runner's conversation loop expects."""
    return {
        "test_id": test_case["id"],
        "name": test_case["title"],
        "category": test_case["category"],
        "description": test_case["expected_result"],
        "conversation": test_case["conversation"],
        "test_case_version": test_case["version"],
    }


def save_all(test_cases, path=STORE_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"test_cases": test_cases}, f, indent=2)


def upsert_via_dashboard(test_case):
    """POST a new/updated test case to the Tex-eval-dashboard API. Requires
    EVAL_DASHBOARD_URL + EVAL_DASHBOARD_KEY (both already set in CI for
    pushing eval results). Returns True on success, False otherwise."""
    url = _dashboard_url()
    key = os.environ.get("EVAL_DASHBOARD_KEY")
    if not url or not key:
        return False

    req = urllib.request.Request(
        f"{url}/api/test-cases",
        data=json.dumps(test_case).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as e:
        print(f"⚠️  Could not save test case '{test_case.get('id')}' to dashboard ({e}); falling back to local store")
        return False


def upsert(test_case, path=STORE_PATH, changed_by="system"):
    """Insert or update a test case, bumping version and appending history
    when an existing entry's content changes."""
    test_cases = load_all(path)
    existing = next((t for t in test_cases if t["id"] == test_case["id"]), None)
    now = _now()

    if existing is None:
        test_case.setdefault("version", 1)
        test_case.setdefault("history", [])
        test_case.setdefault("created_at", now)
        test_case["updated_at"] = now
        test_cases.append(test_case)
    else:
        existing["history"].append({
            "version": existing["version"],
            "changed_at": now,
            "changed_by": changed_by,
            "note": f"status={existing['status']}",
        })
        existing.update(test_case)
        existing["version"] = existing["version"] + 1
        existing["updated_at"] = now

    save_all(test_cases, path)
    return test_case["id"]
