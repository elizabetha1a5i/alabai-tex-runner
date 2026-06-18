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
from google.oauth2.credentials import Credentials
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

ENVIRONMENTS = {
    "staging":    "https://weber-gpt-staging.vercel.app/?wsms=",
    "production": "https://weber-gpt-serverless.vercel.app/",
    "production_web": "https://weberranch.com",
}

# Set at runtime via select_environment() — do not change here.
ENVIRONMENT = "staging"
BASE_URL    = ENVIRONMENTS[ENVIRONMENT]

BROWSER_CONTEXT_PATH = os.path.expanduser("~/.tex_qa_browser")

USER_ISSUES_TAB   = " Known User Issues"
SYSTEM_ISSUES_TAB = "[WARN] Known System Issues"
QA_LOG_TAB        = "🧪 QA Test Log"

VARIANTS_PER_ISSUE = 3
CUSTOM_TEST_VARIANTS = 50

CONSENT_PROMPT_1 = (
    "To better serve you wherever you go, please sign in to connect via text. "
    "Just need your name, date of birth (DD/MM/YYYY), and phone number. "
    "Once you share those I'll suggest a World Cup drink featuring Weber Ranch Vodka."
)
CONSENT_PROMPT_2 = "Could you tell me your full name to finish the sign-up?"

# ============================================================================
# ENVIRONMENT SELECTOR
# ============================================================================

def select_environment():
    """Ask the user which environment to run against. Sets ENVIRONMENT and BASE_URL."""
    global ENVIRONMENT, BASE_URL
    import sys
    # CI mode — honour env var, default to staging
    env_override = os.environ.get("TEST_ENVIRONMENT", "").strip().lower()
    if env_override and env_override in ENVIRONMENTS:
        ENVIRONMENT = env_override
        BASE_URL    = ENVIRONMENTS[ENVIRONMENT]
        print(f"  Environment (CI): {ENVIRONMENT} -> {BASE_URL}")
        return

    if not sys.stdin.isatty():
        print(f"  Environment (default): {ENVIRONMENT} -> {BASE_URL}")
        return

    print("\n" + "="*50)
    print("  SELECT ENVIRONMENT")
    print("="*50)
    options = list(ENVIRONMENTS.keys())
    for i, key in enumerate(options, 1):
        print(f"  {i}. {key:<20} {ENVIRONMENTS[key]}")
    print("="*50)
    while True:
        choice = input("▶ Select environment (1/2/3): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            ENVIRONMENT = options[int(choice) - 1]
            BASE_URL    = ENVIRONMENTS[ENVIRONMENT]
            print(f"  Running against: {ENVIRONMENT} -> {BASE_URL}\n")
            return
        print("  Invalid choice — enter 1, 2, or 3.")


# ============================================================================
# GOOGLE AUTH
# ============================================================================

def get_google_services():
    import json as _json
    creds = None

    # Prefer token.json (works in CI and locally).
    # Fall back to token.pickle for backwards compatibility.
    if os.path.exists("token.json"):
        try:
            with open("token.json") as f:
                raw = f.read().strip()
            if not raw:
                raise ValueError("token.json is empty")
            d = _json.loads(raw)
        except Exception as e:
            print(f"  [WARN]  token.json unreadable ({e}) — will try OAuth flow")
            d = {}
        if d.get("refresh_token"):
            creds = Credentials(
                token=d.get("token"),
                refresh_token=d.get("refresh_token"),
                token_uri=d.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=d.get("client_id"),
                client_secret=d.get("client_secret"),
                scopes=d.get("scopes"),
            )
    elif os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if creds and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            print(f"  [WARN]  Token refresh failed ({e}) — re-authenticating via browser")
            creds = None

    if not creds:
        if not os.path.exists(CREDENTIALS_FILE):
            print("\n[FAIL] credentials.json not found!")
            return None, None
        flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            _json.dump({
                "token":          creds.token,
                "refresh_token":  creds.refresh_token,
                "token_uri":      creds.token_uri,
                "client_id":      creds.client_id,
                "client_secret":  creds.client_secret,
                "scopes":         list(creds.scopes) if creds.scopes else SCOPES,
                "universe_domain": "googleapis.com",
                "account":        "",
                "expiry":         creds.expiry.isoformat() + "Z" if creds.expiry else None,
            }, f)

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
        print(f"[WARN]  Could not load {tab_name}: {e}")
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
# GEMINI — MODEL PICKER + HELPERS
# ============================================================================

_PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-001",
    "gemini-1.5-flash",
]
_selected_model = None


def _get_gemini_client():
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


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
                    print(f"  Gemini model selected: {_selected_model}")
                    return _selected_model
        if available:
            _selected_model = available[0]
            print(f"  Gemini model (fallback): {_selected_model}")
            return _selected_model
    except Exception as e:
        print(f"  Could not list Gemini models: {e}")
    _selected_model = _PREFERRED_MODELS[0]
    return _selected_model


def _gemini_generate(prompt_text, retries=3, delay=5):
    client = _get_gemini_client()
    model  = _pick_gemini_model(client)
    for attempt in range(retries):
        try:
            response = client.models.generate_content(model=model, contents=prompt_text)
            return response.text.strip()
        except Exception as e:
            if attempt < retries - 1:
                print(f"    API error (attempt {attempt+1}/{retries}): {str(e)[:60]} — retrying in {delay}s...")
                time.sleep(delay)
            else:
                raise


# ============================================================================
# GEMINI — GENERATE SCRIPTED TEST
# ============================================================================

def generate_scripted_test(issue, variant_num):
    """Generate a multi-turn scripted conversation to test the issue"""
    prompt = f"""You are a QA engineer testing Weber GPT (Tex), an AI vodka mixologist chatbot via SMS.

Issue to test: "{issue['description']}"
Variant number: {variant_num} of {VARIANTS_PER_ISSUE} (make each variant approach the issue differently)

Generate a realistic multi-turn SMS conversation script (2-3 user messages) that would trigger this behaviour.
Messages should be short and natural — like real SMS texts.

Return ONLY valid JSON, no markdown fences:
{{
  "turns": ["first message", "follow up if needed"],
  "expected": "what Tex should do correctly"
}}"""
    raw  = _gemini_generate(prompt)
    raw  = re.sub(r"^```(?:json)?\s*", "", raw)
    raw  = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    return data.get("turns", []), data.get("expected", "")


def generate_custom_test(test_name, test_description, variant_num):
    """Generate a 50-variant test starting with the consent sign-up flow"""
    prompt = f"""You are a QA engineer testing Weber GPT (Tex), an AI vodka mixologist chatbot via SMS.

Test name: "{test_name}"
Test description: "{test_description}"
Variant number: {variant_num} of {CUSTOM_TEST_VARIANTS} (use different names, DOBs, phone numbers, and phrasing each variant)

Generate a realistic multi-turn SMS conversation that follows this exact flow:
1. CONSENT REPLY: The user replies to Tex's sign-up prompt — "{CONSENT_PROMPT_1}" — by providing a realistic fake name, date of birth (DD/MM/YYYY format), and phone number in a natural SMS style.
2. FULL NAME REPLY: The user replies to Tex's follow-up — "{CONSENT_PROMPT_2}" — with a full name.
3. TEST MESSAGE(S): 1-2 short SMS messages that naturally trigger the scenario in the test description.

Keep all messages short and natural, like real SMS texts. Use varied realistic fake identities across variants.

Return ONLY valid JSON, no markdown fences:
{{
  "turns": ["consent reply with name/dob/phone", "full name reply", "test message"],
  "expected": "what Tex should do correctly"
}}"""
    raw  = _gemini_generate(prompt)
    raw  = re.sub(r"^```(?:json)?\s*", "", raw)
    raw  = re.sub(r"\s*```$", "", raw)
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

Did Tex PASS or FAIL? Return ONLY valid JSON, no markdown fences:
{{"result": "PASS", "notes": "brief reason"}}"""
    raw  = _gemini_generate(prompt)
    raw  = re.sub(r"^```(?:json)?\s*", "", raw)
    raw  = re.sub(r"\s*```$", "", raw)
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
    print("  C   — Custom test (50 variants, type your own)")
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
        range=f"'{QA_LOG_TAB}'!A1:A2000",
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
            print(f"    [MSG] Response at {elapsed:.1f}s")

        if got_resp and time.time() - last_change >= stability and elapsed >= 5:
            print(f"    [OK] Stable at {elapsed:.1f}s")
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
    print(f"\n[DIR] Creating Drive folder: {run_name}")
    folder_id, folder_url = create_drive_folder(drive_service, run_name, DRIVE_FOLDER_ID)
    print(f"[OK] {folder_url}")

    total_pass = total_fail = total_error = 0

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
            print(f"[LIST] [{issue_idx+1}/{len(selected_issues)}] {feedback_id}")
            print(f"   {issue['description'][:60]}...")
            print(f"   Source: {issue['source']}  |  Severity: {issue['severity']}")

            for v_num in range(1, VARIANTS_PER_ISSUE + 1):
                variant_label = chr(64 + v_num)  # A, B, C
                run_id = f"RUN-{feedback_id}-{variant_label}"

                print(f"\n  [{variant_label}] Generating test script...")
                try:
                    turns, generated_expected = generate_scripted_test(issue, v_num)
                except Exception as e:
                    print(f"  [FAIL] Generation failed: {e}")
                    continue

                # Use expected behaviour from sheet if set, otherwise use Claude-generated
                expected = issue.get("expected") or generated_expected
                print(f"  Expected: {expected[:55]}...")

                try:
                    page_text = await run_scripted_test(page, turns)
                except Exception as e:
                    print(f"  [FAIL] Test error: {e}")
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

                icon = "[OK]" if pass_fail == "PASS" else ("[FAIL]" if pass_fail == "FAIL" else "[WARN]")
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
                    print(f"  [PDF] PDF exported: {pdf_name}")
                except Exception as e:
                    print(f"  [WARN]  Could not export PDF: {str(e)[:60]}")

                if pass_fail == "PASS":
                    total_pass += 1
                elif pass_fail == "FAIL":
                    total_fail += 1

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
                    print(f"  [LOG] Written to QA Log row {next_row}")
                except Exception as e:
                    print(f"  [WARN]  Could not write to sheet: {e}")

        await browser.close()

    total = total_pass + total_fail + total_error
    print(f"\n{'='*70}")
    print(f"[STATS] ALIBI AI — RUN COMPLETE")
    print(f"{'='*70}")
    print(f"  Issues tested:  {len(selected_issues)}")
    print(f"  Variants run:   {total}")
    print(f"  [OK] Pass:        {total_pass}")
    print(f"  [FAIL] Fail:        {total_fail}")
    print(f"  [WARN]  Error:       {total_error}")
    print(f"\n[DIR] Drive: {folder_url}")
    print(f"[OK] Results written to QA Test Log")


async def run_custom_test_suite(test_name, test_description, drive_service, sheets_service):
    safe_name  = re.sub(r"[^\w]", "_", test_name)[:30]
    run_name   = f"Custom_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"\n[DIR] Creating Drive folder: {run_name}")
    folder_id, folder_url = create_drive_folder(drive_service, run_name, DRIVE_FOLDER_ID)
    print(f"[OK] {folder_url}")

    feedback_id  = f"CUSTOM-{safe_name.upper()}"
    total_pass = total_fail = total_error = 0

    async with async_playwright() as pw:
        os.makedirs(BROWSER_CONTEXT_PATH, exist_ok=True)
        is_headless = os.environ.get("HEADLESS", "false").lower() == "true"
        browser = await pw.chromium.launch_persistent_context(
            BROWSER_CONTEXT_PATH,
            headless=is_headless,
            viewport={"width": 390, "height": 844},
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        for v_num in range(1, CUSTOM_TEST_VARIANTS + 1):
            variant_label = f"V{v_num:02d}"
            run_id        = f"RUN-{feedback_id}-{variant_label}"

            print(f"\n{'='*70}")
            print(f"  [{v_num}/{CUSTOM_TEST_VARIANTS}] {run_id} — generating script...")

            try:
                turns, expected = generate_custom_test(test_name, test_description, v_num)
            except Exception as e:
                print(f"  [FAIL] Generation failed: {e}")
                total_error += 1
                continue

            print(f"  Expected: {expected[:55]}...")

            try:
                page_text = await run_scripted_test(page, turns)
            except Exception as e:
                print(f"  [FAIL] Test error: {e}")
                page_text = ""

            if not page_text:
                pass_fail, notes = "ERROR", "No response captured"
                total_error += 1
            else:
                try:
                    pass_fail, notes = evaluate_response(page_text, test_description, turns, expected)
                except Exception as e:
                    pass_fail, notes = "ERROR", f"Evaluator error: {str(e)[:60]}"
                    total_error += 1

            icon = "[OK]" if pass_fail == "PASS" else ("[FAIL]" if pass_fail == "FAIL" else "[WARN]")
            print(f"  {icon} {pass_fail}: {notes[:55]}")

            pdf_url = ""
            try:
                date_str = datetime.now().strftime("%d%m%Y")
                pdf_name = f"{run_id}_Tex_Conversation_{date_str}.pdf"
                pdf_path = f"/tmp/{pdf_name}"
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
                print(f"  [PDF] PDF exported: {pdf_name}")
            except Exception as e:
                print(f"  [WARN]  Could not export PDF: {str(e)[:60]}")

            if pass_fail == "PASS":
                total_pass += 1
            elif pass_fail == "FAIL":
                total_fail += 1

            try:
                next_row = get_next_log_row(sheets_service)
                write_log_row(
                    sheets_service, next_row,
                    run_id,
                    datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "custom-test",
                    "Custom",
                    test_description,
                    " | ".join(turns),
                    page_text,
                    expected,
                    pass_fail,
                    notes,
                    feedback_id,
                    variant_label,
                    pdf_url,
                    "",
                )
                print(f"  [LOG] Written to QA Log row {next_row}")
            except Exception as e:
                print(f"  [WARN]  Could not write to sheet: {e}")

        await browser.close()

    total = total_pass + total_fail + total_error
    print(f"\n{'='*70}")
    print(f"[STATS] CUSTOM TEST COMPLETE — {test_name}")
    print(f"{'='*70}")
    print(f"  Variants run:   {total}")
    print(f"  [OK] Pass:        {total_pass}")
    print(f"  [FAIL] Fail:        {total_fail}")
    print(f"  [WARN]  Error:       {total_error}")
    print(f"\n[DIR] Drive: {folder_url}")
    print(f"[OK] Results written to QA Test Log")


def main():
    select_environment()
    print("\n[AUTH] Connecting to Google...")
    drive_service, sheets_service = get_google_services()
    if not drive_service:
        print("[FAIL] Auth failed.")
        return
    print("[OK] Connected")

    # Custom test CI mode — must be checked before loading issues
    custom_name = os.environ.get("CUSTOM_TEST_NAME", "").strip()
    custom_desc = os.environ.get("CUSTOM_TEST_DESCRIPTION", "").strip()
    if custom_name and custom_desc:
        print(f"\n[BOT] CI custom test mode — '{custom_name}'")
        print(f"\n[RUN] Running custom test '{custom_name}' × {CUSTOM_TEST_VARIANTS} variants...")
        asyncio.run(run_custom_test_suite(custom_name, custom_desc, drive_service, sheets_service))
        return

    print("\n[LIST] Loading issues...")
    issues = load_all_issues(sheets_service)

    if not issues:
        print("[OK] No open issues found.")
        return

    # In CI (no TTY) — run all issues automatically
    import sys
    if not sys.stdin.isatty():
        print("\n[BOT] CI mode — running all issues automatically")
        indices = list(range(len(issues)))
        print(f"\n[RUN] Running {len(indices)} issue(s) × {VARIANTS_PER_ISSUE} variants = {len(indices)*VARIANTS_PER_ISSUE} tests...")
        asyncio.run(run_selected_issues(indices, issues, drive_service, sheets_service))
        return

    while True:
        show_menu(issues)
        selection = input("\n▶ Enter selection: ").strip()

        if selection.upper() == "Q":
            print("Goodbye!")
            break

        if selection.upper() == "C":
            test_name = input("\n▶ Test name: ").strip()
            if not test_name:
                print("[FAIL] Test name required.")
                continue
            test_description = input("▶ What should this test be about?\n  > ").strip()
            if not test_description:
                print("[FAIL] Test description required.")
                continue
            print(f"\n[RUN] Running custom test '{test_name}' × {CUSTOM_TEST_VARIANTS} variants...")
            asyncio.run(run_custom_test_suite(test_name, test_description, drive_service, sheets_service))
            again = input("\n▶ Run more? (y/n): ").strip().lower()
            if again != "y":
                break
            continue

        indices = parse_selection(selection, issues)
        if indices is None:
            break
        if not indices:
            print("[FAIL] No valid selection.")
            continue

        print(f"\n[RUN] Running {len(indices)} issue(s) × {VARIANTS_PER_ISSUE} variants = {len(indices)*VARIANTS_PER_ISSUE} tests...")
        asyncio.run(run_selected_issues(indices, issues, drive_service, sheets_service))

        again = input("\n▶ Run more? (y/n): ").strip().lower()
        if again != "y":
            break


if __name__ == "__main__":
    main()
