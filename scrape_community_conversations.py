"""
Community.com Conversation Scraper — READ ONLY
────────────────────────────────────────────────
Logs into dashboard.community.com, scrolls the Messages inbox (newest-first)
to find conversations within a date range, opens each one, and reads back
the transcript text. Feeds directly into analyze_community_csat.py's
Gemini-scoring + dashboard-push pipeline (score_and_push()) — same output
shape as its CSV path.

HARD INVARIANT: this script is read-only. It only ever:
  - fills the login form (email/password)
  - clicks conversation rows in the left list
  - scrolls the conversation list / reads text from the transcript pane
It NEVER fills, clicks, or otherwise interacts with the message-compose box,
send button, emoji/GIF/attachment controls, or any other write action.
Do not add such interactions to this script.

Selectors below are best-effort from screenshots only and have NOT been
verified against the live site yet — run with --dry-run first and expect
to need adjustment once you see real output.

Usage:
    python scrape_community_conversations.py --from 2026-07-20 --to 2026-07-27 --dry-run
    python scrape_community_conversations.py --from 2026-07-20 --to 2026-07-27
"""

import argparse
import asyncio
import os
import re
from datetime import datetime, timedelta, timezone

from playwright.async_api import async_playwright

from analyze_community_csat import score_and_push

LOGIN_URL = "https://dashboard.community.com/login"
INBOX_URL_PREFIX = "https://dashboard.community.com/messages"

# Best-effort candidate selectors — adjust once run against the real site.
EMAIL_INPUT_SELECTORS = ['input[placeholder="Email"]', 'input[type="email"]']
PASSWORD_INPUT_SELECTORS = ['input[placeholder="Password"]', 'input[type="password"]']
LOGIN_BUTTON_SELECTORS = ['button:has-text("Login")']

CONVERSATION_LIST_SELECTORS = ['[class*="conversation-list"]', '[class*="MessageList"]', 'main >> nth=0']
CONVERSATION_ROW_SELECTORS = ['[class*="conversation-row"]', '[class*="MessageListItem"]']

TRANSCRIPT_PANE_SELECTORS = ['[class*="transcript"]', '[class*="MessageThread"]', 'main >> nth=1']

# Explicitly never touched — documented here so it's obvious what NOT to add.
NEVER_INTERACT_WITH = [
    'input[placeholder*="Send a message"]',
    'button:has-text("Send")',
]


def _parse_relative_timestamp(label: str, now: datetime) -> datetime:
    """Community's list shows things like '9:29 AM', 'Yesterday', 'Friday',
    or an absolute date. Best-effort parse into a real datetime."""
    label = label.strip()

    time_match = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)$", label, re.IGNORECASE)
    if time_match:
        hour, minute, ampm = time_match.groups()
        hour = int(hour) % 12
        if ampm.upper() == "PM":
            hour += 12
        return now.replace(hour=hour, minute=int(minute), second=0, microsecond=0)

    if label.lower() == "yesterday":
        return (now - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)

    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if label.lower() in weekdays:
        target = weekdays.index(label.lower())
        days_back = (now.weekday() - target) % 7
        days_back = days_back or 7  # a bare weekday name means "last <weekday>", not today
        return (now - timedelta(days=days_back)).replace(hour=12, minute=0, second=0, microsecond=0)

    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y"):
        try:
            return datetime.strptime(label, fmt)
        except ValueError:
            continue

    return now  # unrecognized format — treat as "now" so it isn't skipped silently


async def _login(page, email, password, client_name):
    await page.goto(LOGIN_URL)

    for selector in EMAIL_INPUT_SELECTORS:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2000):
                await el.click()
                await el.fill(email)
                break
        except Exception:
            continue

    for selector in PASSWORD_INPUT_SELECTORS:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2000):
                await el.click()
                await el.fill(password)
                break
        except Exception:
            continue

    for selector in LOGIN_BUTTON_SELECTORS:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                break
        except Exception:
            continue

    # This account manages multiple clients/brands — login lands on a
    # "Select Account" page first (rows: avatar, name, phone number, chevron).
    # The visible text isn't itself clickable — walk up to the nearest
    # button/link/role=button ancestor that wraps the whole row.
    await page.wait_for_load_state("load", timeout=15000)
    if "/client-selection" in page.url:
        clicked = await _click_account_row(page, client_name)
        if not clicked:
            raise RuntimeError(
                f"Could not find a clickable row for account '{client_name}' on /client-selection."
            )

    await page.wait_for_url(f"{INBOX_URL_PREFIX}**", timeout=15000)


async def _click_account_row(page, account_name):
    """Clicks the account row on the Select Account page. The name text
    sits inside a row (avatar + name + phone + chevron) that's wrapped in
    some clickable ancestor — try common wrapper tags first, then fall
    back to a forced click directly on the text."""
    xpath_candidates = [
        f'xpath=//*[contains(normalize-space(text()), "{account_name}")]/ancestor::button[1]',
        f'xpath=//*[contains(normalize-space(text()), "{account_name}")]/ancestor::a[1]',
        f'xpath=//*[contains(normalize-space(text()), "{account_name}")]/ancestor::*[@role="button"][1]',
    ]
    for xp in xpath_candidates:
        try:
            el = page.locator(xp).first
            if await el.is_visible(timeout=2000):
                await el.click()
                return True
        except Exception:
            continue

    try:
        text_el = page.get_by_text(account_name, exact=True).first
        await text_el.click(force=True, timeout=2000)
        return True
    except Exception:
        return False


async def _find_first_visible(page, selectors):
    for selector in selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2000):
                return el
        except Exception:
            continue
    return None


async def _collect_rows_in_range(page, date_from, date_to, now):
    """Scrolls the conversation list (newest-first) and returns row locators
    + parsed timestamps for conversations within [date_from, date_to]."""
    list_el = await _find_first_visible(page, CONVERSATION_LIST_SELECTORS)
    if list_el is None:
        raise RuntimeError("Could not find the conversation list — selectors need updating.")

    in_range = []
    seen_texts = set()
    stable_scrolls = 0

    while stable_scrolls < 3:
        rows = page.locator(", ".join(CONVERSATION_ROW_SELECTORS))
        count = await rows.count()
        new_this_pass = 0

        for i in range(count):
            row = rows.nth(i)
            try:
                row_text = await row.inner_text(timeout=1000)
            except Exception:
                continue
            if row_text in seen_texts:
                continue
            seen_texts.add(row_text)
            new_this_pass += 1

            timestamp_match = re.search(r"\n(.+?)\n", row_text)
            label = timestamp_match.group(1) if timestamp_match else ""
            occurred_at = _parse_relative_timestamp(label, now)

            if occurred_at < date_from:
                return in_range  # newest-first list — past this point, all older
            if occurred_at <= date_to:
                in_range.append((row, occurred_at, row_text))

        if new_this_pass == 0:
            stable_scrolls += 1
        else:
            stable_scrolls = 0

        await list_el.evaluate("(el) => el.scrollBy(0, el.clientHeight)")
        await page.wait_for_timeout(600)

    return in_range


async def _scrape_thread(page, row):
    await row.click()
    await page.wait_for_timeout(1200)  # let the transcript pane load

    pane = await _find_first_visible(page, TRANSCRIPT_PANE_SELECTORS)
    if pane is None:
        return ""
    try:
        return await pane.inner_text(timeout=3000)
    except Exception:
        return ""


async def scrape(date_from: datetime, date_to: datetime, dry_run: bool, client_name: str):
    email = os.environ.get("COMMUNITY_EMAIL")
    password = os.environ.get("COMMUNITY_PASSWORD")
    if not email or not password:
        raise SystemExit("COMMUNITY_EMAIL / COMMUNITY_PASSWORD must be set.")

    now = datetime.now(timezone.utc)
    conversations = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print(f"🔐 Logging into Community.com (client: {client_name})...")
        await _login(page, email, password, client_name)
        print("✅ Logged in.")

        print(f"📜 Scrolling conversation list for {date_from.date()} → {date_to.date()}...")
        rows = await _collect_rows_in_range(page, date_from, date_to, now)
        print(f"   Found {len(rows)} conversation(s) in range.")

        if dry_run:
            for _, occurred_at, row_text in rows:
                sender = row_text.split("\n")[0] if row_text else "?"
                print(f"   [dry-run] {occurred_at.isoformat()} — {sender}")
            await browser.close()
            return []

        for i, (row, occurred_at, row_text) in enumerate(rows, 1):
            sender = row_text.split("\n")[0] if row_text else "?"
            print(f"   Reading {i}/{len(rows)} ({sender})...")
            transcript_text = await _scrape_thread(page, row)
            if not transcript_text:
                print(f"   ⚠️  Could not read transcript for {sender}, skipping.")
                continue
            conversations.append({
                "external_id": f"{sender}-{occurred_at.isoformat()}",
                "customer_ref": sender,
                "occurred_at": occurred_at.isoformat(),
                "transcript_text": transcript_text,
            })

        await browser.close()

    return conversations


def main():
    parser = argparse.ArgumentParser(description="Read-only scrape of Community.com conversations for CSAT scoring")
    parser.add_argument("--from", dest="date_from", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", required=True, help="End date, YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="List conversations in range without opening/scoring them")
    parser.add_argument("--client", default="Weber Ranch", help="Client/brand name to select after login, if a client-selection step appears")
    args = parser.parse_args()

    date_from = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    date_to = datetime.strptime(args.date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)

    conversations = asyncio.run(scrape(date_from, date_to, args.dry_run, args.client))

    if args.dry_run:
        print("\n✅ Dry run complete — no conversations opened or scored.")
        return

    score_and_push(conversations, source_label=f"community-scrape-{args.date_from}-to-{args.date_to}")


if __name__ == "__main__":
    main()
