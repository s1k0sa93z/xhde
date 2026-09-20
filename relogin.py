"""
Logs into the Mind Recalls Moodle portal FRESH every run, then finds the
newest OTHER active session (i.e. the previous run's login, or any other
active login) and logs it out. This naturally rotates through one live
session at a time: each run's fresh login becomes "current" (and thus
un-killable by itself), while the run's own fresh session is used to kill
off the prior session, which is no longer current.

Intended to be run on a schedule (e.g. every 5 minutes via GitHub Actions).
Each run is fully independent -- no session state is persisted or reused
between runs.

Credentials are read from environment variables:
  MINDRECALLS_USERNAME
  MINDRECALLS_PASSWORD

Usage:
  python relogin.py
"""

import os
import sys
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

LOGIN_URL = "https://portal.mindrecalls.com/login/index.php"
BASE_URL = "https://portal.mindrecalls.com"
SESSIONS_URL = f"{BASE_URL}/report/usersessions/user.php"

# Text Moodle uses to mark whichever row is the CURRENT session on this page
# (i.e. the session actually making this request). We exclude this row from
# ever being logged out -- we only ever target OTHER sessions.
CURRENT_SESSION_MARKERS = ["This session", "Current session"]

NAV_TIMEOUT_MS = 45000


def goto_sessions_page(page):
    """Navigate to the sessions page robustly. Uses 'domcontentloaded'
    instead of 'networkidle', since sites with background polling/analytics
    can prevent the network from ever going fully idle, causing spurious
    timeouts even though the page itself loaded fine. We then explicitly
    wait for either a sessions table row or the login form to appear, which
    tells us definitively that the page is actually ready."""
    page.goto(SESSIONS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    try:
        page.wait_for_selector(
            "table, #login, form#login",
            timeout=NAV_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError:
        print("WARNING: Timed out waiting for expected page content, continuing anyway.")


def is_logged_in(page) -> bool:
    """Check whether we actually landed on the sessions page (vs. being
    bounced to the login page)."""
    return "login/index.php" not in page.url


def do_login(page, username, password):
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    page.wait_for_selector("#username", timeout=NAV_TIMEOUT_MS)
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#loginbtn")

    try:
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass

    if "login/index.php" in page.url:
        print("ERROR: Login appears to have failed. Check credentials or page structure.")
        print(page.content()[:2000])
        sys.exit(1)

    print(f"Logged in successfully. Landed on: {page.url}")


def main():
    username = os.environ.get("MINDRECALLS_USERNAME")
    password = os.environ.get("MINDRECALLS_PASSWORD")

    if not username or not password:
        print("ERROR: MINDRECALLS_USERNAME and MINDRECALLS_PASSWORD must be set as env vars.")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.set_default_timeout(NAV_TIMEOUT_MS)
        page = context.new_page()

        # --- Always log in fresh this run ---
        do_login(page, username, password)
        goto_sessions_page(page)

        if not is_logged_in(page):
            print("ERROR: Still not logged in after login attempt. Aborting.")
            print(page.content()[:2000])
            browser.close()
            sys.exit(1)

        # --- Inspect session rows that have a delete link ---
        # The sessions table lists rows newest-first, and we only ever need
        # the newest non-current one, so there's no reason to pull the whole
        # table (which can be long) -- the first 10 rows are always enough.
        MAX_ROWS_TO_CHECK = 10
        all_rows_with_delete = page.locator("tr:has(a[href*='delete='])")
        total_row_count = all_rows_with_delete.count()
        row_count = min(total_row_count, MAX_ROWS_TO_CHECK)
        print(f"Found {total_row_count} session row(s) with a delete link; checking the first {row_count}.")

        if row_count == 0:
            print("No deletable session rows found (only this fresh login exists). Nothing to log out this run.")
            browser.close()
            return

        # Debug visibility: print each row's visible text.
        other_rows = []
        for i in range(row_count):
            row = all_rows_with_delete.nth(i)
            text = row.inner_text().replace("\n", " | ")
            is_current = any(marker in text for marker in CURRENT_SESSION_MARKERS)
            print(f"  Row {i}: current={is_current} | text=\"{text[:150]}\"")
            if not is_current:
                other_rows.append(row)

        if len(other_rows) == 0:
            print("Only the current (just-logged-in) session was found. Nothing else to log out.")
            browser.close()
            return

        # ASSUMPTION: the sessions table lists rows newest-first, so the
        # first "other" row is the most recent non-current session (i.e.
        # almost always the previous run's login).
        target_row = other_rows[0]
        target_text = target_row.inner_text().replace("\n", " | ")
        print(f"Targeting this session to log out: \"{target_text[:150]}\"")

        delete_link = target_row.locator("a[href*='delete=']").first
        href = delete_link.get_attribute("href")
        print(f"Delete link (for reference): {href}")

        # Click the actual link element (rather than navigating to its href
        # directly) so any JS-driven confirmation/submission behavior the
        # site relies on actually fires, the same as a real user clicking it.
        delete_link.click()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            print("WARNING: Timed out waiting for page after clicking logout link, continuing anyway.")

        print(f"Clicked logout link for target session. Final URL: {page.url}")

        # Verify the row is actually gone now
        delete_id = href.split("delete=")[1].split("&")[0]
        goto_sessions_page(page)
        still_present = page.locator(f"tr:has(a[href*='delete={delete_id}'])").count()
        if still_present > 0:
            print("WARNING: Target session still appears in the list after clicking logout. Deletion may not have worked.")
        else:
            print("Confirmed: target session no longer appears in the list.")

        browser.close()


if __name__ == "__main__":
    main()
