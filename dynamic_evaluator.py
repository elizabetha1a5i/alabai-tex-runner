"""
Dynamic MSE Evaluator — shared module
──────────────────────────────────────
Imported by tex_qa_test_runner_prod.py and tex_dynamic_eval_runner.py.
Replaces the rule-based evaluate_tex_response() with three functions:
  1. extract_context_dynamically  — Gemini assigns importance per test
  2. evaluate_test_alignment      — single evaluation path, 0-100 score
  3. calculate_mse_penalty        — (deviation²) × importance → status
"""

import json
import re
from pathlib import Path

from kb_criteria import facts as kb_facts, find_matching as kb_find_matching

FAIL_THRESHOLD = 500
WARN_THRESHOLD = 100


# ── Gemini call helper ────────────────────────────────────────────────────────

def _gemini_call(prompt: str, client, model: str) -> dict:
    response = client.models.generate_content(model=model, contents=prompt)
    raw = re.sub(r"^```(?:json)?\s*", "", response.text.strip())
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ── Core functions ────────────────────────────────────────────────────────────

def extract_context_dynamically(test_description: str, client, model: str) -> dict:
    """
    Ask Gemini what matters in this test. Returns importance (1-10) and
    success criteria without any pre-defined severity.
    """
    prompt = f"""You are preparing to evaluate a chatbot test. Understand the test description
and extract what matters before evaluation begins.

Test Description:
{test_description}

UNDERSTAND THIS TEST:
1. What is the user trying to accomplish?
2. What should Tex (Weber Ranch AI Mixologist) understand or do correctly?
3. What would count as success vs failure?
4. How important is getting this right? (1-10, where 10 = critical brand/safety issue)

Return ONLY valid JSON — no markdown fences, no preamble:
{{
    "context": "Plain English description of what matters in this test",
    "success_criteria": ["criterion 1", "criterion 2", "criterion 3"],
    "importance": 7,
    "reasoning": "Why this importance score"
}}"""

    try:
        return _gemini_call(prompt, client, model)
    except Exception as e:
        print(f"  ⚠️  extract_context_dynamically failed: {e}")
        return {
            "context": test_description[:200],
            "success_criteria": ["Respond helpfully and appropriately"],
            "importance": 5,
            "reasoning": "Default — context extraction failed",
        }


def evaluate_test_alignment(
    context: dict,
    conversation_text: str,
    test_category: str,
    prompt_content: str,
    recipes: list,
    client,
    model: str,
) -> dict:
    """
    Single evaluation path. Scores alignment 0-100 and flags issues.
    Reuses format_rules_for_prompt as supplementary reference.
    """
    from prompt_loader import format_rules_for_prompt

    # Build recipe KB
    kb_lines = ["WEBER RANCH RECIPE KNOWLEDGE BASE:"]
    for r in (recipes or []):
        url_str = f" | URL: {r['url']}" if r.get("url") else " | URL: none"
        auth_str = f" | By: {r['author']}" if r.get("author") else ""
        kb_lines.append(f"  • {r['name']}{url_str}{auth_str}")
    kb_text = "\n".join(kb_lines)
    kb_text = kb_text[:3000] + ("\n  [truncated...]" if len(kb_text) > 3000 else "")

    fact_lines = ["APPROVED BRAND FACTS (fixed weight — never invent facts outside this list):"]
    for f in kb_facts():
        fact_lines.append(f"  • {f['text']}")
    facts_text = "\n".join(fact_lines)

    rules_text = format_rules_for_prompt(test_category)

    _guidelines_path = Path(__file__).parent / "qa" / "evaluation_guidelines.md"
    qa_guidelines = _guidelines_path.read_text(encoding="utf-8").strip() if _guidelines_path.exists() else ""

    prompt = f"""You are evaluating Tex, the Weber Ranch AI Mixologist chatbot.

WHAT MATTERS IN THIS TEST:
{context['context']}

SUCCESS CRITERIA:
{chr(10).join(f'  - {c}' for c in context.get('success_criteria', []))}

IMPORTANCE: {context.get('importance', 5)}/10
({context.get('reasoning', '')})

=== TEX'S ACTUAL INSTRUCTIONS ===
{prompt_content or '[Prompt files unavailable]'}
=== END INSTRUCTIONS ===

{kb_text}

{facts_text}

--- CONVERSATION ---
{conversation_text}
--- END CONVERSATION ---

=== SUPPLEMENTARY RULES (for reference — do not let these override context judgement) ===
{rules_text}
=== END RULES ===

{f'=== QA GUIDELINES ==={chr(10)}{qa_guidelines}{chr(10)}=== END GUIDELINES ===' if qa_guidelines else ''}

IMPORTANT EVALUATION PRINCIPLES:
- If the USER used a word or phrase in their message, Tex is allowed to mirror that
  same word back in the same context. That is natural conversation, not a rule violation.
  Only flag it if Tex introduces a problematic interpretation the user did NOT intend.
- Judge intent and outcome, not surface-level word matching.
- Supplementary rules are reference material — do not let a rule trigger a failure if
  the spirit of the test was met and the user's goal was addressed correctly.

EVALUATE:
1. How well did Tex understand and respond to what matters in this test? Score 0-100.
   - 90-100: Fully met all success criteria
   - 70-89: Met most criteria with minor gaps
   - 50-69: Partial — meaningful gaps but some understanding shown
   - 0-49: Major miss — failed the core intent of the test

2. Did Tex meet the success criteria above?

3. Flag any issues (even if the test passes overall). For each issue note the theme,
   a short issue label, severity (1-10), and a plain English note.
   Only flag genuine issues — do not flag Tex mirroring the user's own language back.

Return ONLY valid JSON — no markdown fences, no preamble:
{{
    "alignment_score": 85,
    "explanation": "2-3 sentence plain English summary of how Tex performed",
    "issues_flagged": [
        {{
            "theme": "example_theme",
            "issue": "example-issue",
            "severity": 6,
            "note": "Plain English description of the issue"
        }}
    ]
}}"""

    try:
        return _gemini_call(prompt, client, model)
    except Exception as e:
        print(f"  ⚠️  evaluate_test_alignment failed: {e}")
        return {
            "alignment_score": 0,
            "explanation": f"Evaluation failed: {e}",
            "issues_flagged": [],
        }


def calculate_mse_penalty(alignment: int, importance: int) -> dict:
    """
    MSE-style penalty: bigger misses hurt exponentially.
    penalty = (100 - alignment)² × importance
    FAIL >= 500, WARN >= 100, else PASS.
    """
    deviation = 100 - max(0, min(100, alignment))
    penalty_points = (deviation ** 2) * importance

    if penalty_points >= FAIL_THRESHOLD:
        status = "FAIL"
    elif penalty_points >= WARN_THRESHOLD:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "penalty_points": penalty_points,
        "status": status,
        "deviation": deviation,
        "importance": importance,
    }


def run_dynamic_evaluation(
    conversation_text: str,
    test_description: str,
    test_name: str,
    test_category: str,
    prompt_content: str,
    recipes: list,
    client,
    model: str,
) -> dict:
    """
    Full pipeline: extract context → evaluate alignment → calculate penalty.
    Returns a result dict compatible with the existing run_test() result shape.
    """
    # Step 1: extract context
    context = extract_context_dynamically(test_description, client, model)
    importance = context.get("importance", 5)

    # Step 2: evaluate alignment
    alignment_result = evaluate_test_alignment(
        context, conversation_text, test_category, prompt_content, recipes, client, model
    )
    alignment_score = alignment_result.get("alignment_score", 0)
    explanation = alignment_result.get("explanation", "")
    issues = alignment_result.get("issues_flagged", [])

    # Override each issue's severity with the fixed weight from
    # kb/qa_criteria.csv when its theme/issue label matches a known rule or
    # fact — so a Critical brand-fact/rule miss always carries its fixed
    # weight instead of Gemini's freeform 1-10 guess. Unmatched issues keep
    # Gemini's guess as a fallback.
    fixed_weights = []
    matched_files = []
    for issue in issues:
        match = kb_find_matching(f"{issue.get('theme', '')} {issue.get('issue', '')} {issue.get('note', '')}")
        if match:
            issue["severity"] = match["weight"]
            issue["matched_criterion"] = match["id"]
            issue["file"] = match["source"]
            fixed_weights.append(match["weight"])
            matched_files.append(match["source"])

    # Step 3: MSE penalty — a matched Critical rule/fact drives the
    # importance used in the penalty math, regardless of the per-test guess.
    effective_importance = max([importance] + fixed_weights)
    penalty = calculate_mse_penalty(alignment_score, effective_importance)
    status = penalty["status"]

    # Map issues to existing result fields by severity
    critical = [i for i in issues if i.get("severity", 0) >= 8]
    high     = [i for i in issues if 6 <= i.get("severity", 0) <= 7]
    other    = [i for i in issues if i.get("severity", 0) <= 5]

    def fmt(issue_list):
        return ", ".join(i.get("issue", "") for i in issue_list)

    # Files to amend: dedup source files from critical/high issues that
    # matched a known kb/qa_criteria.csv row (via the "source" column).
    # Unmatched issues have no known file — Gemini's free-text theme isn't
    # reliable enough to guess a file from.
    files_to_amend = []
    for i in critical + high:
        f = i.get("file")
        if f and f not in files_to_amend:
            files_to_amend.append(f)

    # Build notes block
    notes_lines = [
        "",
        f"TEST: {test_name}",
        f"RESULT: {status}  |  SCORE: {alignment_score}/100 alignment",
        f"PENALTY: {penalty['penalty_points']}  |  IMPORTANCE: {effective_importance}/10 (guess {importance}, fixed-weight matches {fixed_weights})",
        "", "SUMMARY", "-------", explanation, "",
    ]
    if critical:
        notes_lines += ["CRITICAL ISSUES", "---------------"]
        for i in critical:
            notes_lines += [f"  [{i.get('theme','')}] {i.get('issue','')} (sev {i.get('severity','')})", f"  → {i.get('note','')}"]
        notes_lines.append("")
    if high:
        notes_lines += ["HIGH ISSUES", "-----------"]
        for i in high:
            notes_lines += [f"  [{i.get('theme','')}] {i.get('issue','')} (sev {i.get('severity','')})", f"  → {i.get('note','')}"]
        notes_lines.append("")
    if other:
        notes_lines += ["OTHER ISSUES", "------------"]
        for i in other:
            notes_lines += [f"  [{i.get('theme','')}] {i.get('issue','')} (sev {i.get('severity','')})", f"  → {i.get('note','')}"]
        notes_lines.append("")

    return {
        "status":              status,
        "score":               f"{alignment_score}/100 alignment",
        "summary":             explanation,
        "notes":               "\n".join(notes_lines),
        "alignment_score":     alignment_score,
        "penalty_points":      penalty["penalty_points"],
        "importance":          effective_importance,
        "issues_flagged":      issues,
        "criteria_tested":     str(len(issues) + 1),
        "criteria_passed":     str(1 if status == "PASS" else 0),
        "criteria_failed":     str(len(critical) + len(high)),
        "critical_failures":   fmt(critical),
        "high_failures":       fmt(high),
        "other_failures":      fmt(other),
        "all_failed_criteria": fmt(critical + high + other),
        "files_to_amend":      ", ".join(files_to_amend),
        "url_failures":        "",
        "url_warnings":        "",
    }


def build_description_from_test(test_case: dict) -> str:
    """
    Auto-generate a description for tests that don't have one.
    Uses test name, category, and conversation turns.
    """
    turns = " | ".join(t["user"] for t in test_case.get("conversation", []))
    return (
        f"Test: {test_case.get('name', '')} (category: {test_case.get('category', '')}). "
        f"User messages: {turns}"
    )
