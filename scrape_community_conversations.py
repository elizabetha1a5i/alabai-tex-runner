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

CONVERSATION_LIST_SELECTORS = ['[data-testid="inbox-list-main"]', '[class*="conversation-list"]', '[class*="MessageList"]', 'main >> nth=0']
# Confirmed via DevTools: each row is <button data-testid="inbox-item" id="<uuid>">.
CONVERSATION_ROW_SELECTORS = ['[data-testid="inbox-item"]', '[class*="conversation-row"]', '[class*="MessageListItem"]']

TRANSCRIPT_PANE_SELECTORS = [
    '[class*="Convo__StyledMain"]',  # confirmed via DevTools: wraps the bubble thread
    '[class*="transcript"]',
    '[class*="MessageThread"]',
    'main >> nth=1',
]
# Individual message bubbles within the transcript pane — confirmed via
# DevTools: data-testid="convo-bubble-<uuid>", class starts with
# "ConvoBubble__StyledConvoBubbleRoot".
CONVO_BUBBLE_SELECTOR = '[data-testid^="convo-bubble-"]'

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
    # "Select Account" page first (confirmed via DevTools: rows are real
    # <button role="button"> elements). This app is client-side-routed, so
    # the URL changes via history.pushState with no reliable 'load' event —
    # poll for either destination instead of checking page.url immediately
    # (checking too early was silently skipping the click entirely, since
    # the redirect from /login hadn't happened yet).
    route_deadline = asyncio.get_event_loop().time() + 15
    routed_to = None
    while asyncio.get_event_loop().time() < route_deadline:
        if "/client-selection" in page.url:
            routed_to = "client-selection"
            break
        if page.url.startswith(INBOX_URL_PREFIX):
            routed_to = "inbox"
            break
        await page.wait_for_timeout(300)
    print(f"   [debug] post-login routed_to={routed_to}, url={page.url}")

    if routed_to == "client-selection":
        clicked = await _click_by_text(page, client_name)
        print(f"   [debug] clicked account row: {clicked}, url right after click: {page.url}")
        if not clicked:
            raise RuntimeError(
                f"Could not find a clickable row for account '{client_name}' on /client-selection."
            )
    elif routed_to is None:
        await page.screenshot(path="debug_after_login.png", full_page=True)
        with open("debug_after_login.html", "w", encoding="utf-8") as f:
            f.write(await page.content())
        raise RuntimeError(
            f"Never routed to /client-selection or {INBOX_URL_PREFIX} after login. "
            f"Still at: {page.url}. See debug_after_login.png/.html artifacts — login form/button selectors may be wrong."
        )

    # Selecting an account (or logging straight in for a single-account user)
    # lands on the account's Home dashboard, not Messages directly — that
    # only happens when the login URL carries a next=/messages/... param,
    # which ours doesn't. Click "Messages" in the sidebar nav to get there.
    # Poll page.url() directly rather than wait_for_url(): this is a
    # client-side-routed SPA, so navigation may happen via history.pushState
    # without a 'load' event.
    messages_clicked = False
    deadline = asyncio.get_event_loop().time() + 20
    while asyncio.get_event_loop().time() < deadline:
        if page.url.startswith(INBOX_URL_PREFIX):
            return
        if not messages_clicked:
            messages_clicked = await _click_by_text(page, "Messages")
            print(f"   [debug] clicked Messages nav: {messages_clicked}, url: {page.url}")
        await page.wait_for_timeout(500)

    # Still not on the inbox — save a screenshot + full HTML for debugging
    # instead of failing blind again.
    await page.screenshot(path="debug_after_account_click.png", full_page=True)
    with open("debug_after_account_click.html", "w", encoding="utf-8") as f:
        f.write(await page.content())
    raise RuntimeError(
        f"Never reached {INBOX_URL_PREFIX} after account selection / Messages nav click. "
        f"Still at: {page.url}. See debug_after_account_click.png/.html artifacts."
    )


async def _click_by_text(page, text, roles=("button", "link")):
    """Generic clicker: find an interactive element whose accessible name
    contains `text` (tries each role in `roles` first, e.g. button/link),
    falling back to an xpath ancestor search and finally a forced click on
    the raw text node. Verbose [debug] prints so failures are diagnosable
    without another guess-and-CI-run cycle. Used both for the account-
    selection row and the "Messages" sidebar nav link — same underlying
    problem (find the clickable element for some visible text)."""
    for role in roles:
        try:
            role_matches = page.get_by_role(role, name=text)
            count = await role_matches.count()
            print(f"   [debug] get_by_role('{role}', name='{text}') matched {count} element(s)")
            if count > 0:
                el = role_matches.first
                visible = await el.is_visible(timeout=3000)
                print(f"   [debug] first match visible: {visible}")
                if visible:
                    await el.click(timeout=5000)
                    print(f"   [debug] .click() on {role} match completed without raising")
                    return True
        except Exception as e:
            print(f"   [debug] get_by_role('{role}', ...) attempt raised: {e!r}")

    # Fallback in case the role/name lookup doesn't match (e.g. wording changes).
    xpath_candidates = [
        f'xpath=//*[contains(normalize-space(text()), "{text}")]/ancestor::button[1]',
        f'xpath=//*[contains(normalize-space(text()), "{text}")]/ancestor::a[1]',
        f'xpath=//*[contains(normalize-space(text()), "{text}")]/ancestor::*[@role="button"][1]',
    ]
    for xp in xpath_candidates:
        try:
            el = page.locator(xp)
            count = await el.count()
            print(f"   [debug] xpath candidate matched {count} element(s): {xp}")
            if count > 0 and await el.first.is_visible(timeout=2000):
                await el.first.click(timeout=5000)
                print("   [debug] .click() on xpath match completed without raising")
                return True
        except Exception as e:
            print(f"   [debug] xpath candidate raised: {e!r}")
            continue

    try:
        text_el = page.get_by_text(text, exact=True)
        count = await text_el.count()
        print(f"   [debug] get_by_text matched {count} element(s)")
        await text_el.first.click(force=True, timeout=5000)
        print("   [debug] forced click on text match completed without raising")
        return True
    except Exception as e:
        print(f"   [debug] forced text click raised: {e!r}")
        return False


async def _find_first_visible(page, selectors, label=""):
    for selector in selectors:
        try:
            count = await page.locator(selector).count()
            el = page.locator(selector).first
            visible = await el.is_visible(timeout=2000) if count > 0 else False
            print(f"   [debug] {label} selector '{selector}': {count} match(es), first visible: {visible}")
            if visible:
                return el
        except Exception as e:
            print(f"   [debug] {label} selector '{selector}' raised: {e!r}")
            continue
    return None


async def _collect_rows_in_range(page, date_from, date_to, now):
    """Scrolls the conversation list (newest-first) and returns row locators
    + parsed timestamps for conversations within [date_from, date_to]."""
    list_el = await _find_first_visible(page, CONVERSATION_LIST_SELECTORS, label="conversation-list")
    if list_el is None:
        raise RuntimeError("Could not find the conversation list — selectors need updating.")

    in_range = []
    seen_texts = set()
    stable_scrolls = 0

    pass_num = 0
    while stable_scrolls < 3:
        pass_num += 1
        rows = page.locator(", ".join(CONVERSATION_ROW_SELECTORS))
        count = await rows.count()
        print(f"   [debug] scroll pass {pass_num}: {count} row(s) matched by CONVERSATION_ROW_SELECTORS")
        new_this_pass = 0

        for i in range(count):
            row = rows.nth(i)
            try:
                row_text = await row.inner_text(timeout=1000)
            except Exception as e:
                print(f"   [debug] row {i} inner_text() raised: {e!r}")
                continue
            if i < 2 and pass_num == 1:
                print(f"   [debug] sample row {i} text: {row_text!r}")
            if row_text in seen_texts:
                continue
            seen_texts.add(row_text)
            new_this_pass += 1

            # Confirmed row shape (DevTools sample): avatar initials, then
            # name, then timestamp, then message preview — e.g.
            # "EA\nelizabeth alabi\n9:29 AM\nDo you know what Love Island is?"
            lines = [l.strip() for l in row_text.split("\n") if l.strip()]
            sender = lines[1] if len(lines) > 1 else (lines[0] if lines else "?")
            label = lines[2] if len(lines) > 2 else ""
            occurred_at = _parse_relative_timestamp(label, now)

            if occurred_at < date_from:
                return in_range  # newest-first list — past this point, all older
            if occurred_at <= date_to:
                row_id = await row.get_attribute("id")
                in_range.append((row, occurred_at, row_text, row_id, sender))

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

    bubbles = page.locator(CONVO_BUBBLE_SELECTOR)
    bubble_count = await bubbles.count()
    print(f"   [debug] {CONVO_BUBBLE_SELECTOR}: {bubble_count} bubble(s)")
    if bubble_count > 0:
        lines = []
        for i in range(bubble_count):
            try:
                lines.append(await bubbles.nth(i).inner_text(timeout=1000))
            except Exception:
                continue
        if lines:
            return "\n".join(lines)

    # Fall back to whatever the whole pane's text looks like.
    pane = await _find_first_visible(page, TRANSCRIPT_PANE_SELECTORS, label="transcript-pane")
    if pane is None:
        return ""
    try:
        return await pane.inner_text(timeout=3000)
    except Exception:
        return ""


async def scrape(date_from: datetime, date_to: datetime, dry_run: bool, client_name: str, headed: bool = False):
    email = os.environ.get("COMMUNITY_EMAIL")
    password = os.environ.get("COMMUNITY_PASSWORD")
    if not email or not password:
        raise SystemExit("COMMUNITY_EMAIL / COMMUNITY_PASSWORD must be set.")

    now = datetime.now(timezone.utc)
    conversations = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed, slow_mo=300 if headed else 0)
        page = await browser.new_page()

        print(f"🔐 Logging into Community.com (client: {client_name})...")
        await _login(page, email, password, client_name)
        print("✅ Logged in.")

        print(f"📜 Scrolling conversation list for {date_from.date()} → {date_to.date()}...")
        rows = await _collect_rows_in_range(page, date_from, date_to, now)
        print(f"   Found {len(rows)} conversation(s) in range.")

        if dry_run:
            for _, occurred_at, row_text, row_id, sender in rows:
                print(f"   [dry-run] {occurred_at.isoformat()} — {sender} (id={row_id})")
            await browser.close()
            return []

        for i, (row, occurred_at, row_text, row_id, sender) in enumerate(rows, 1):
            print(f"   Reading {i}/{len(rows)} ({sender})...")
            transcript_text = await _scrape_thread(page, row)
            if not transcript_text:
                print(f"   ⚠️  Could not read transcript for {sender}, skipping.")
                continue
            conversations.append({
                "external_id": row_id or f"{sender}-{occurred_at.isoformat()}",
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
    parser.add_argument("--headed", action="store_true", help="Show the browser window (for local debugging — don't use in CI)")
    args = parser.parse_args()

    date_from = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    date_to = datetime.strptime(args.date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)

    conversations = asyncio.run(scrape(date_from, date_to, args.dry_run, args.client, args.headed))

    if args.dry_run:
        print("\n✅ Dry run complete — no conversations opened or scored.")
        return

    score_and_push(conversations, source_label=f"community-scrape-{args.date_from}-to-{args.date_to}")


if __name__ == "__main__":
    main()
