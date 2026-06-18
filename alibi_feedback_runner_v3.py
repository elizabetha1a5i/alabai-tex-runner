"""
alibi_feedback_runner_v3.py
────────────────────────────
Alibi AI — Feedback Test Runner v3 for Weber GPT (Tex)

- Interactive menu to pick which issues to run
- No consent opener — raw test messages only
- Multi-turn scripted conversations per issue
- Reads from Known User Issues + Known System Issues
- Writes results to QA Test Log
"""

import asyncio
import csv
import json
import os
import re
import time
import pickle
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from google import genai

# ============================================================================
# CONFIGURATION
# ============================================================================

DRIVE_FOLDER_ID = "1RcWyUsG3FrEkSpLkeqkVZsWVPpF6vUOy"
SPREADSHEET_ID  = "1x0WAJ_1v5eTamaFSNgkkrNYurxEQTfbU3TdWkqCmXw0"

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE       = "token.pickle"

ENVIRONMENT = "staging"
BASE_URL    = "https://weber-gpt-staging.vercel.app/?wsms="

BROWSER_CONTEXT_PATH = os.path.expanduser("~/.tex_qa_browser")

USER_ISSUES_TAB   = " Known User Issues"
SYSTEM_ISSUES_TAB = "⚠️ Known System Issues"
QA_LOG_TAB        = "🧪 QA Test Log"

VARIANTS_PER_ISSUE = 3

# ============================================================================
# GOOGLE AUTH
# ============================================================================

def get_google_services():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print("\n❌ credentials.json not found!")
                return None, None
            flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return build("drive", "v3", credentials=creds), build("sheets", "v4", credentials=creds)

# ============================================================================
# LOAD ISSUES
# ============================================================================

def load_sheet(sheets_service, tab_name):
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{tab_name}'!A1:Z200",
        ).execute()
        return result.get("values", [])
    except Exception as e:
        print(f"⚠️  Could not load {tab_name}: {e}")
        return []


def load_all_issues(sheets_service):
    issues = []

    # User issues
    rows = load_sheet(sheets_service, USER_ISSUES_TAB)
    for i, row in enumerate(rows):
        if i < 1:
            continue
        while len(row) < 6:
            row.append("")
        # Columns: A=Severity, B=Date, C=Request From, D=Expected Behaviour, E=Description, F=Prompt/File Fix, G=Status
        while len(row) < 7:
            row.append("")
        severity, date, source, expected, desc, fix, status = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
        if desc.strip() and desc.strip() not in ("Description", "") and status.strip().lower() not in ("resolved", "closed", "done"):
            issues.append({
                "type":        "User",
                "severity":    severity.strip() or "Medium",
                "date":        date.strip(),
                "source":      source.strip(),
                "description": desc.strip(),
                "expected":    expected.strip(),
                "prompt_fix":  fix.strip(),
                "status":      status.strip(),
            })

    # System issues
    rows = load_sheet(sheets_service, SYSTEM_ISSUES_TAB)
    for i, row in enumerate(rows):
        if i < 2:
            continue
        while len(row) < 6:
            row.append("")
        issue, files, severity, desc, fix, status = row[0], row[1], row[2], row[3], row[4], row[5]
        if desc.strip() and status.strip().lower() not in ("resolved", "closed", "done"):
            issues.append({
                "type":        "System",
                "severity":    severity.strip() or "Medium",
                "date":        datetime.now().strftime("%d/%m/%y"),
                "source":      "System",
                "description": desc.strip(),
                "prompt_fix":  fix.strip(),
                "files":       files.strip(),
                "status":      status.strip(),
            })

    return issues

# ============================================================================
# CLAUDE — GENERATE SCRIPTED TEST
# ============================================================================

def api_call_with_retry(fn, retries=3, delay=5):
    """Retry an API call on connection errors"""
    import time
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if attempt < retries - 1:
                print(f"    ⚠️  API error (attempt {attempt+1}/{retries}): {str(e)[:60]} — retrying in {delay}s...")
                time.sleep(delay)
            else:
                raise


_PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-001",
    "gemini-1.5-flash",
]

def _pick_gemini_model():
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
    for model in _PREFERRED_MODELS:
        try:
            client.models.get(model=model)
            return model
        except Exception:
            continue
    return _PREFERRED_MODELS[0]

_gemini_model = None

def _gemini_generate(prompt, max_tokens=500):
    global _gemini_model
    api_key = os.environ.get("GEMINI_API_KEY", "")
    client = genai.Client(api_key=api_key)
    if _gemini_model is None:
        _gemini_model = _pick_gemini_model()
    response = client.models.generate_content(
        model=_gemini_model,
        contents=prompt,
    )
    return response.text.strip()


def generate_scripted_test(issue, variant_num):
    """Generate a multi-turn scripted conversation to test the issue"""
    prompt = f"""You are a QA engineer testing Weber GPT (Tex), an AI vodka mixologist chatbot via SMS.

Issue to test: "{issue['description']}"
Variant number: {variant_num} of {VARIANTS_PER_ISSUE} (make each variant approach the issue differently)

Generate a realistic multi-turn SMS conversation script (2-3 user messages) that would trigger this behaviour.
Messages should be short and natural -- like real SMS texts.

Return ONLY valid JSON:
{{
  "turns": ["first message", "follow up if needed"],
  "expected": "what Tex should do correctly"
}}"""
    raw = _gemini_generate(prompt, max_tokens=500)
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    return data.get("turns", []), data.get("expected", "")


def evaluate_response(page_text, issue_description, test_turns, expected):
    conversation = "\n".join([f"User: {t}" for t in test_turns])
    prompt = f"""You are a QA evaluator for Weber GPT (Tex).

ISSUE BEING TESTED: {issue_description}
TEST CONVERSATION:
{conversation}
EXPECTED: {expected}
TEX PAGE CONTENT: {page_text[:800]}

Did Tex PASS or FAIL? Return ONLY valid JSON:
{{"result": "PASS", "notes": "brief reason"}}"""
    raw = _gemini_generate(prompt, max_tokens=300)
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    return data.get("result", "WARN"), data.get("notes", "")

# ============================================================================
# MENU
# ============================================================================

def show_menu(issues):
    print("\n" + "="*70)
    print("🧪 ALIBI AI — FEEDBACK TEST RUNNER v3")
    print("="*70)
    print(f"\n{'#':<4} {'Type':<8} {'Source':<12} {'Issue':<45} {'Status'}")
    print("-"*70)
    for i, issue in enumerate(issues, 1):
        desc = issue["description"][:44]
        print(f"  {i:<3} {issue['type']:<8} {issue['source']:<12} {desc:<45} {issue['status'] or 'Open'}")
    print("\n" + "="*70)
    print("  ALL — Run all issues")
    print("  U   — All user issues")
    print("  S   — All system issues")
    print("  Q   — Quit")
    print("  Or enter numbers: '1', '1,3,5', '1-4'")
    print("="*70)


def parse_selection(selection, issues):
    s = selection.strip().upper()
    if s == "Q":
        return None
    if s == "ALL":
        return list(range(len(issues)))
    if s == "U":
        return [i for i, issue in enumerate(issues) if issue["type"] == "User"]
    if s == "S":
        return [i for i, issue in enumerate(issues) if issue["type"] == "System"]

    indices = []
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-")
            indices.extend(range(int(start.strip()) - 1, int(end.strip())))
        else:
            try:
                indices.append(int(part) - 1)
            except:
                pass
    return indices

# ============================================================================
# GOOGLE DRIVE
# ============================================================================

def create_drive_folder(service, name, parent_id):
    meta   = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    folder = service.files().create(body=meta, fields="id").execute()
    fid    = folder["id"]
    service.permissions().create(fileId=fid, body={"role": "reader", "type": "anyone"}).execute()
    return fid, f"https://drive.google.com/drive/folders/{fid}"


def upload_to_drive(service, file_path, folder_id):
    mime_map  = {".png": "image/png", ".md": "text/markdown", ".csv": "text/csv"}
    mime_type = mime_map.get(Path(file_path).suffix, "application/octet-stream")
    media = MediaFileUpload(file_path, mimetype=mime_type)
    meta  = {"name": os.path.basename(file_path), "parents": [folder_id]}
    file  = service.files().create(body=meta, media_body=media, fields="id").execute()
    fid   = file["id"]
    service.permissions().create(fileId=fid, body={"role": "reader", "type": "anyone"}).execute()
    return f"https://drive.google.com/file/d/{fid}/view"

# ============================================================================
# SHEET WRITE
# ============================================================================

def get_next_log_row(sheets_service):
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{QA_LOG_TAB}'!A1:A500",
    ).execute()
    return len(result.get("values", [])) + 1


def write_log_row(sheets_service, row_num, run_id, date, prompt_file, source,
                  feedback, test_input, tex_response, expected,
                  pass_fail, notes, feedback_id, variant, pdf_url="", png_url=""):
    range_notation = f"'{QA_LOG_TAB}'!A{row_num}:O{row_num}"
    body = {"values": [[
        run_id, date, prompt_file, source,
        feedback,
        test_input,
        tex_response[:500] if tex_response else "",
        expected,
        pass_fail,
        notes[:300] if notes else "",
        "Feedback-Driven",
        "Alibi AI",
        f"{feedback_id}-{variant}",
        pdf_url,
        png_url,
    ]]}
    sheets_service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_notation,
        valueInputOption="RAW",
        body=body,
    ).execute()

# ============================================================================
# PLAYWRIGHT
# ============================================================================

async def wait_for_response(page, max_wait=120, stability=10):
    start       = time.time()
    last_change = start
    prev        = ""
    got_resp    = False
    bursts      = 0

    await page.wait_for_timeout(3000)
    try:
        prev = await page.inner_text("body")
    except:
        pass

    while True:
        elapsed = time.time() - start
        if elapsed > max_wait:
            return elapsed
        try:
            cur = await page.inner_text("body")
        except:
            cur = prev
        if cur != prev:
            last_change = time.time()
            got_resp    = True
            bursts     += 1
            prev        = cur
            print(f"    📨 Response at {elapsed:.1f}s")

        if got_resp and time.time() - last_change >= stability and elapsed >= 5:
            print(f"    ✅ Stable at {elapsed:.1f}s")
            return elapsed

        await page.wait_for_timeout(1500)


async def get_page_text(page):
    try:
        body = await page.inner_text("body")
        ui_noise = {
            "what is tex?", "world's best martini?", "what is weber ranch vodka?",
            "type your message...", "thinking...", "howdy!", "tex",
            "your weber ranch ai mixologist",
        }
        lines    = [l.strip() for l in body.split("\n") if l.strip()]
        filtered = [l for l in lines if l.lower() not in ui_noise and len(l) > 5]
        return "\n".join(filtered)
    except:
        return ""


async def run_scripted_test(page, turns):
    """Run a multi-turn conversation and return the final page text"""
    await page.goto(BASE_URL)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)

    for i, turn in enumerate(turns):
        elem = None
        for sel in ['input[placeholder*="message"]', 'input[type="text"]', 'textarea']:
            elem = await page.query_selector(sel)
            if elem:
                break

        if not elem:
            return ""

        await elem.click()
        await elem.fill(turn)
        await elem.press("Enter")
        print(f"    Turn {i+1}: {turn[:50]}...")
        await wait_for_response(page)

    return await get_page_text(page)

# ============================================================================
# MAIN RUNNER
# ============================================================================

async def run_selected_issues(selected_issues, all_issues, drive_service, sheets_service):
    run_name = f"Alibi_Feedback_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"\n📁 Creating Drive folder: {run_name}")
    folder_id, folder_url = create_drive_folder(drive_service, run_name, DRIVE_FOLDER_ID)
    print(f"✅ {folder_url}")

    total_pass = total_fail = total_error = 0
    csv_rows = []

    async with async_playwright() as pw:
        os.makedirs(BROWSER_CONTEXT_PATH, exist_ok=True)
        is_headless = os.environ.get("HEADLESS", "false").lower() == "true"
        browser = await pw.chromium.launch_persistent_context(
            BROWSER_CONTEXT_PATH,
            headless=is_headless,
            viewport={"width": 390, "height": 844},
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        for issue_idx, idx in enumerate(selected_issues):
            issue = all_issues[idx]
            feedback_id = f"FB-{issue['type'].upper()}-{idx+1:03d}"

            print(f"\n{'='*70}")
            print(f"📋 [{issue_idx+1}/{len(selected_issues)}] {feedback_id}")
            print(f"   {issue['description'][:60]}...")
            print(f"   Source: {issue['source']}  |  Severity: {issue['severity']}")

            for v_num in range(1, VARIANTS_PER_ISSUE + 1):
                variant_label = chr(64 + v_num)  # A, B, C
                run_id = f"RUN-{feedback_id}-{variant_label}"

                print(f"\n  [{variant_label}] Generating test script...")
                try:
                    turns, generated_expected = generate_scripted_test(issue, v_num)
                except Exception as e:
                    print(f"  ❌ Generation failed: {e}")
                    continue

                # Use expected behaviour from sheet if set, otherwise use Claude-generated
                expected = issue.get("expected") or generated_expected
                print(f"  Expected: {expected[:55]}...")

                try:
                    page_text = await run_scripted_test(page, turns)
                except Exception as e:
                    print(f"  ❌ Test error: {e}")
                    page_text = ""

                if not page_text:
                    pass_fail, notes = "ERROR", "No response captured"
                    total_error += 1
                else:
                    try:
                        pass_fail, notes = evaluate_response(
                            page_text, issue["description"], turns, expected
                        )
                    except Exception as e:
                        pass_fail, notes = "ERROR", f"Evaluator error: {str(e)[:60]}"
                        total_error += 1

                icon = "✅" if pass_fail == "PASS" else ("❌" if pass_fail == "FAIL" else "⚠️")
                print(f"  {icon} {pass_fail}: {notes[:55]}")

                # Export PDF (print quality — no screenshots)
                pdf_url = ""
                png_url = ""
                try:
                    date_str = datetime.now().strftime("%d%m%Y")
                    pdf_name = f"{run_id}_Tex_Conversation_{date_str}.pdf"
                    pdf_path = f"/tmp/{pdf_name}"
                    # Scroll to top so full conversation prints from beginning
                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(500)
                    await page.pdf(
                        path=pdf_path,
                        print_background=True,
                        format="A4",
                        margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
                    )
                    pdf_url = upload_to_drive(drive_service, pdf_path, folder_id)
                    os.remove(pdf_path)
                    print(f"  📄 PDF exported: {pdf_name}")
                except Exception as e:
                    print(f"  ⚠️  Could not export PDF: {str(e)[:60]}")

                if pass_fail == "PASS":
                    total_pass += 1
                elif pass_fail == "FAIL":
                    total_fail += 1

                csv_rows.append({
                    "test_id":    run_id,
                    "name":       issue["description"][:80],
                    "category":   issue.get("source", "Feedback"),
                    "date":       datetime.now().strftime("%Y-%m-%d"),
                    "environment": os.environ.get("TEST_ENVIRONMENT", "staging"),
                    "status":     pass_fail,
                    "score":      "",
                    "notes":      notes,
                    "summary":    notes,
                })

                # Write to sheet
                try:
                    next_row = get_next_log_row(sheets_service)
                    write_log_row(
                        sheets_service, next_row,
                        run_id,
                        datetime.now().strftime("%d/%m/%Y %H:%M"),
                        issue.get("prompt_fix") or issue.get("files") or "core-rules.md",
                        issue["source"],
                        issue["description"],
                        " | ".join(turns),
                        page_text,
                        expected,
                        pass_fail,
                        notes,
                        feedback_id,
                        variant_label,
                        pdf_url,
                        png_url,
                    )
                    print(f"  📝 Written to QA Log row {next_row}")
                except Exception as e:
                    print(f"  ⚠️  Could not write to sheet: {e}")

        await browser.close()

    if csv_rows:
        csv_path = "tex_feedback_results.csv"
        fieldnames = ["test_id", "name", "category", "date", "environment", "status", "score", "notes", "summary"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"\n[CSV] Results saved: {csv_path}")

    total = total_pass + total_fail + total_error
    print(f"\n{'='*70}")
    print(f"📈 ALIBI AI — RUN COMPLETE")
    print(f"{'='*70}")
    print(f"  Issues tested:  {len(selected_issues)}")
    print(f"  Variants run:   {total}")
    print(f"  ✅ Pass:        {total_pass}")
    print(f"  ❌ Fail:        {total_fail}")
    print(f"  ⚠️  Error:       {total_error}")
    print(f"\n📁 Drive: {folder_url}")
    print(f"✅ Results written to QA Test Log")


def main():
    print("\n🔐 Connecting to Google...")
    drive_service, sheets_service = get_google_services()
    if not drive_service:
        print("❌ Auth failed.")
        return
    print("✅ Connected")

    print("\n📋 Loading issues...")
    issues = load_all_issues(sheets_service)

    if not issues:
        print("✅ No open issues found.")
        return

    # In CI (no TTY) — run all issues automatically
    import sys
    if not sys.stdin.isatty():
        print("\n🤖 CI mode — running all issues automatically")
        indices = list(range(len(issues)))
        print(f"\n🚀 Running {len(indices)} issue(s) × {VARIANTS_PER_ISSUE} variants = {len(indices)*VARIANTS_PER_ISSUE} tests...")
        asyncio.run(run_selected_issues(indices, issues, drive_service, sheets_service))
        return

    while True:
        show_menu(issues)
        selection = input("\n▶ Enter selection: ").strip()

        if selection.upper() == "Q":
            print("Goodbye!")
            break

        indices = parse_selection(selection, issues)
        if indices is None:
            break
        if not indices:
            print("❌ No valid selection.")
            continue

        print(f"\n🚀 Running {len(indices)} issue(s) × {VARIANTS_PER_ISSUE} variants = {len(indices)*VARIANTS_PER_ISSUE} tests...")
        asyncio.run(run_selected_issues(indices, issues, drive_service, sheets_service))

        again = input("\n▶ Run more? (y/n): ").strip().lower()
        if again != "y":
            break


if __name__ == "__main__":
    main()
