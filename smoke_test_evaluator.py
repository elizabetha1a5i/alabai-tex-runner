"""
Smoke test for the Gemini evaluator.
Run locally: set GEMINI_API_KEY in your env, then: python smoke_test_evaluator.py
No browser, no Google Sheets — just verifies the Gemini API key works and JSON parses correctly.
"""

import json
import os
import re
import sys

from google import genai


FAKE_CONVERSATION = """\
User: Can you suggest a cocktail?

Tex: Of course! I'd love to help you find the perfect drink. Try the Weber Ranch Mule — \
it's one of our most popular cocktails and a real crowd-pleaser.

Here's what you'll need:
Weber Ranch Vodka, 50ml
Ginger beer, 150ml
Fresh lime juice, 15ml
Mint sprig (optional)
Ice

To make it:
Fill a copper mug with ice. Pour in the Weber Ranch Vodka and fresh lime juice. \
Top with ginger beer and stir gently. Garnish with a mint sprig if you like.

Serve in: A copper mug

Enjoy responsibly. Cheers!
"""

FAKE_CRITERIA = {
    "DATA-01": {
        "name": "Cocktail name stated",
        "rule": "The name of the cocktail must be clearly stated in the response.",
        "severity": "CRITICAL",
    },
    "SCHEMA-01": {
        "name": "Plain text only — no markdown",
        "rule": "Response must use plain text only. No markdown formatting, no bullet points, no headers, no asterisks.",
        "severity": "HIGH",
    },
    "BRAND-01": {
        "name": "Brand name used correctly",
        "rule": "The brand must always be referred to as 'Weber Ranch' — never 'Webber Ranch' or any other misspelling.",
        "severity": "CRITICAL",
    },
}


def run():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set in environment.")
        print("Set it and re-run:  set GEMINI_API_KEY=your-key-here  (Windows)")
        sys.exit(1)

    print("API key found.")
    print("Sending fake Tex response to Gemini for evaluation...\n")

    criteria_lines = "\n".join(
        f"  {cid} [{c['severity']}] {c['name']}: {c['rule']}"
        for cid, c in FAKE_CRITERIA.items()
    )

    prompt = f"""You are a QA evaluator for Tex, the Weber Ranch AI Mixologist chatbot.

Evaluate ONLY the AGENT responses against the criteria listed.
Do not evaluate user messages.

TEST NAME: Smoke Test — Simple Cocktail Suggestion

--- CONVERSATION START ---
{FAKE_CONVERSATION}
--- CONVERSATION END ---

CRITERIA:
{criteria_lines}

EVALUATION RULES:
1. For each criterion decide PASS or FAIL based on what the agent actually said.
2. If a criterion is genuinely not triggered, mark pass: true with note "N/A —".
3. Critical failures -> overall FAIL. High failures only -> overall WARN. Otherwise PASS.

Return ONLY valid JSON — no markdown fences, no preamble:
{{
  "results": [
    {{"id": "DATA-01", "pass": true, "note": "Cocktail named clearly stated"}},
    {{"id": "SCHEMA-01", "pass": true, "note": "Plain text, no markdown detected"}},
    {{"id": "BRAND-01", "pass": true, "note": "Weber Ranch spelled correctly"}}
  ],
  "overall": "PASS",
  "critical_failures": [],
  "high_failures": [],
  "summary": "2-3 sentence plain English summary of how Tex performed."
}}"""

    try:
        gemini = genai.Client(api_key=api_key)
        response = gemini.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        raw = re.sub(r"^```(?:json)?\s*", "", response.text.strip())
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"FAIL — Gemini responded but JSON parse failed: {e}")
        print(f"\nRaw response (first 500 chars):\n{response.text[:500]}")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL — Gemini API call failed: {e}")
        sys.exit(1)

    print(f"Overall: {data.get('overall', '?')}")
    print()
    for r in data.get("results", []):
        icon = "PASS" if r.get("pass") else "FAIL"
        print(f"  [{icon}] {r['id']} — {r.get('note', '')}")
    print()
    print(f"Summary: {data.get('summary', '')}")
    print()
    print("Evaluator is working — safe to run full tests.")


if __name__ == "__main__":
    run()
