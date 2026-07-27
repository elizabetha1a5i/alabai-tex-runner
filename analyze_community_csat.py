"""
Community.com CSAT Analyzer
────────────────────────────
Scores real customer SMS conversations (manually exported from Community.com
as a CSV) for satisfaction/sentiment, and pushes results to the
Tex-eval-dashboard's Postgres-backed /api/csat endpoint — the same push
mechanism already used to send eval results (EVAL_DASHBOARD_URL / KEY).

This is distinct from the QA test runner: it analyzes real customer
conversations after the fact, not scripted test cases.

NOTE: the exact column layout of a Community.com export hasn't been
confirmed yet. This script expects, at minimum, columns for a conversation
identifier, a timestamp, and the message text (see CSV_COLUMN_ALIASES below
to adjust once a real sample export is available).

Usage:
    python analyze_community_csat.py --csv community_export.csv
"""

import argparse
import csv
import json
import os
import re
import urllib.request
from collections import defaultdict
from datetime import datetime

from google import genai

from dynamic_evaluator import run_dynamic_evaluation
from prompt_loader import load_prompts_for_category

# Column names this script will look for, in priority order, per logical
# field. Adjust once a real Community.com export is available.
CSV_COLUMN_ALIASES = {
    "thread_id": ["thread_id", "conversation_id", "id"],
    "customer_ref": ["phone", "phone_number", "customer", "customer_id", "from"],
    "timestamp": ["timestamp", "date", "sent_at", "created_at"],
    "message": ["message", "text", "body", "content"],
    "direction": ["direction", "sender", "from_type"],  # inbound/outbound if present
}

_PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-001",
    "gemini-1.5-flash",
]
_selected_model = None


def _pick_gemini_model(client):
    global _selected_model
    if _selected_model:
        return _selected_model
    try:
        available = []
        for m in client.models.list():
            name = m.name
            if name.startswith("models/"):
                name = name[len("models/"):]
            available.append(name)
        for preferred in _PREFERRED_MODELS:
            for avail in available:
                if avail == preferred or avail.startswith(preferred + "-"):
                    _selected_model = avail
                    return _selected_model
        if available:
            _selected_model = available[0]
            return _selected_model
    except Exception:
        pass
    _selected_model = _PREFERRED_MODELS[0]
    return _selected_model


def _load_recipe_kb():
    """Loads (prompt_content, recipes) for the rule-alignment evaluation,
    reusing tex_qa_test_runner_prod.py's Google Sheets/local-kb loaders
    exactly as the QA test runner does. Imported lazily so the CSV-only
    path doesn't require playwright/google-api-python-client to be
    installed if rule-scoring isn't being used. Returns (None, []) if
    Google credentials aren't available — rule-scoring is skipped in that
    case but CSAT scoring still proceeds."""
    try:
        from tex_qa_test_runner_prod import get_google_services, load_recipes
    except Exception as e:
        print(f"⚠️  Could not import QA runner for rule-scoring: {e}")
        return None, []

    _, sheets_service = get_google_services()
    if sheets_service is None:
        print("⚠️  No Google credentials — skipping rule-based QA scoring (CSAT scoring still runs).")
        return None, []

    prompt_content, prompt_error = load_prompts_for_category("General")
    if prompt_error:
        print(f"⚠️  Could not load Tex's prompt instructions: {prompt_error}")

    recipes = load_recipes(sheets_service)
    return prompt_content, recipes


def _compute_no_reply(transcript_text):
    """True if the conversation ends on the customer's turn with no Tex
    follow-up — a critical failure (Tex went silent). Expects lines
    labeled "Customer: ..."/"Tex: ..." (see load_conversations_from_csv
    and scrape_community_conversations.py's _scrape_thread)."""
    lines = [l for l in transcript_text.split("\n") if l.strip()]
    if not lines:
        return False
    return lines[-1].strip().lower().startswith("customer:")


def _find_column(fieldnames, aliases):
    lower = {f.lower(): f for f in fieldnames}
    for alias in aliases:
        if alias in lower:
            return lower[alias]
    return None


def load_conversations_from_csv(csv_path):
    """Reads the export CSV and groups rows into conversations by thread id
    (or treats each row as its own conversation if no thread id column is
    found — i.e. the export is already one row per conversation)."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        cols = {key: _find_column(fieldnames, aliases) for key, aliases in CSV_COLUMN_ALIASES.items()}

        if not cols["message"]:
            raise ValueError(
                f"Could not find a message/text column in {csv_path}. "
                f"Found columns: {fieldnames}. Update CSV_COLUMN_ALIASES to match."
            )

        threads = defaultdict(list)
        for i, row in enumerate(reader):
            thread_id = row.get(cols["thread_id"]) if cols["thread_id"] else f"row-{i}"
            threads[thread_id].append(row)

    conversations = []
    for thread_id, rows in threads.items():
        lines = []
        for row in rows:
            raw_direction = (row.get(cols["direction"], "") if cols["direction"] else "").strip().lower()
            # Normalize to the same "Customer:"/"Tex:" labels used by
            # scrape_community_conversations.py, so both sources produce the
            # same transcript shape (needed for _compute_no_reply and the
            # rule-alignment evaluator).
            if raw_direction in ("inbound", "customer", "user", "from_customer"):
                prefix = "Customer: "
            elif raw_direction in ("outbound", "tex", "agent", "from_tex"):
                prefix = "Tex: "
            elif raw_direction:
                prefix = f"{raw_direction}: "
            else:
                prefix = ""
            lines.append(f"{prefix}{row.get(cols['message'], '')}")
        timestamp = rows[0].get(cols["timestamp"]) if cols["timestamp"] else None
        customer_ref = rows[0].get(cols["customer_ref"]) if cols["customer_ref"] else None
        conversations.append({
            "external_id": str(thread_id),
            "customer_ref": customer_ref,
            "occurred_at": timestamp,
            "transcript_text": "\n".join(lines),
        })
    return conversations


def _gemini_score(transcript_text, client, model):
    prompt = f"""You are scoring a real customer service SMS conversation for satisfaction.

Conversation:
{transcript_text}

Return ONLY a JSON object with these fields:
- csat_score: integer 1-5 (1=very dissatisfied, 5=very satisfied)
- sentiment: "positive", "neutral", or "negative"
- resolved: true or false — did the conversation reach a satisfying conclusion?
- key_themes: a short comma-separated list of topics/issues raised
- summary: one sentence summarizing the interaction
"""
    response = client.models.generate_content(model=model, contents=prompt)
    raw = re.sub(r"^```(?:json)?\s*", "", response.text.strip())
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def push_to_dashboard(conversations, source_file):
    url = os.environ.get("EVAL_DASHBOARD_URL", "").rstrip("/")
    key = os.environ.get("EVAL_DASHBOARD_KEY")
    if not url or not key:
        print("⚠️  EVAL_DASHBOARD_URL/EVAL_DASHBOARD_KEY not set — skipping push, printing results instead.")
        print(json.dumps(conversations, indent=2))
        return

    for c in conversations:
        c["source_file"] = source_file

    req = urllib.request.Request(
        f"{url}/api/csat",
        data=json.dumps({"conversations": conversations}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"✅ Pushed to dashboard: {json.load(resp)}")


def score_and_push(conversations, source_label):
    """Scores each conversation with Gemini and pushes results to the
    dashboard. Shared by the CSV-import path and any other conversation
    source (e.g. scrape_community_conversations.py) so there's one scoring/
    push implementation. `conversations` must be dicts with at least
    external_id, transcript_text (customer_ref/occurred_at optional)."""
    if not conversations:
        print("❌ No conversations to score. Aborting.")
        return

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = _pick_gemini_model(client)
    print(f"🤖 Gemini model: {model}")

    # Same rule-based QA scoring used for synthetic test cases
    # (evaluation_guidelines.md: brand-fact accuracy, PPI handling, persona,
    # no-proactive-mocktail, etc.), applied here to real conversations.
    # Loaded once, not per conversation. Degrades gracefully (rule-scoring
    # skipped, CSAT scoring still runs) if Google credentials are unavailable.
    prompt_content, recipes = _load_recipe_kb()

    scored = []
    for i, conv in enumerate(conversations, 1):
        print(f"   Scoring {i}/{len(conversations)} ({conv['external_id']})...")
        try:
            result = _gemini_score(conv["transcript_text"], client, model)
        except Exception as e:
            print(f"   ⚠️  Could not score conversation {conv['external_id']}: {e}")
            continue

        no_reply = conv.get("no_reply")
        if no_reply is None:
            no_reply = _compute_no_reply(conv["transcript_text"])

        qa_status = None
        qa_alignment_score = None
        qa_issues = None
        if prompt_content is not None:
            try:
                # test_description is normally a synthetic test's pre-written
                # pass/fail criteria — for a real conversation, the transcript
                # itself is passed instead, so Gemini infers context/success
                # criteria/importance directly from what actually happened.
                qa_result = run_dynamic_evaluation(
                    conversation_text=conv["transcript_text"],
                    test_description=conv["transcript_text"],
                    test_name=conv.get("customer_ref") or conv["external_id"],
                    test_category="General",
                    prompt_content=prompt_content,
                    recipes=recipes,
                    client=client,
                    model=model,
                )
                qa_status = qa_result.get("status")
                qa_alignment_score = qa_result.get("alignment_score")
                qa_issues = qa_result.get("all_failed_criteria") or None
            except Exception as e:
                print(f"   ⚠️  Rule-based QA scoring failed for {conv['external_id']}: {e}")

        scored.append({
            **conv,
            "csat_score": result.get("csat_score"),
            "sentiment": result.get("sentiment"),
            "resolved": result.get("resolved"),
            "key_themes": ", ".join(result["key_themes"]) if isinstance(result.get("key_themes"), list) else result.get("key_themes"),
            "summary": result.get("summary"),
            "no_reply": no_reply,
            "qa_status": qa_status,
            "qa_alignment_score": qa_alignment_score,
            "qa_issues": qa_issues,
        })

    if not scored:
        print("❌ No conversations were scored. Aborting.")
        return

    push_to_dashboard(scored, source_file=source_label)
    print(f"\n✅ Done — {len(scored)} conversation(s) scored and pushed.")


def main():
    parser = argparse.ArgumentParser(description="Score Community.com SMS export conversations for CSAT")
    parser.add_argument("--csv", required=True, help="Path to a manually exported Community.com CSV")
    args = parser.parse_args()

    print(f"📄 Reading {args.csv}...")
    conversations = load_conversations_from_csv(args.csv)
    print(f"   Found {len(conversations)} conversation(s)")

    score_and_push(conversations, source_label=os.path.basename(args.csv))


if __name__ == "__main__":
    main()
