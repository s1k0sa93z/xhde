"""
Maintains ONE persistent login session on the Mind Recalls Moodle portal,
and on every scheduled run, finds the newest OTHER active session (i.e. not
the persistent one this script maintains) and logs that session out.

How it works across runs:
  - Session cookies are saved to storage_state.json after login.
  - GitHub Actions caches that file between runs (see the workflow file),
    so subsequent runs reuse the same login instead of logging in again.
  - If the saved session has expired (e.g. Moodle timed it out), the script
    automatically logs in again and saves a fresh session.

Credentials are read from environment variables:
  MINDRECALLS_USERNAME
  MINDRECALLS_PASSWORD

Usage:
  python relogin.py
"""

import os
import sys
import json
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://portal.mindrecalls.com/login/index.php"
BASE_URL = "https://portal.mindrecalls.com"
SESSIONS_URL = f"{BASE_URL}/report/usersessions/user.php"
STATE_FILE = "storage_state.json"

# Text Moodle uses to mark whichever row is the CURRENT session on this page.
# We exclude this row from ever being logged out.
CURRENT_SESSION_MARKERS = ["This session", "Current session"]


def is_logged_in(page) -> bool:
    """After navigating to the sessions page, check whether we actually
    landed on it (vs. being bounced to the login page, which means our
    saved session was no longer valid)."""
    return "login/index.php" not in page.url


def do_login(page, username, password):
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#loginbtn")
    page.wait_for_load_state("networkidle")

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

        # --- Try to reuse a saved session from a previous run ---
        have_saved_state = os.path.exists(STATE_FILE)
        if have_saved_state:
            print(f"Found saved session state ({STATE_FILE}), attempting to reuse it.")
            context = browser.new_context(storage_state=STATE_FILE)
        else:
            print("No saved session state found. Will perform a fresh login.")
            context = browser.new_context()

        page = context.new_page()

        if have_saved_state:
            page.goto(SESSIONS_URL, wait_until="networkidle")
            if not is_logged_in(page):
                print("Saved session appears to have expired. Logging in fresh.")
                do_login(page, username, password)
                page.goto(SESSIONS_URL, wait_until="networkidle")
        else:
            do_login(page, username, password)
            page.goto(SESSIONS_URL, wait_until="networkidle")

        if not is_logged_in(page):
            print("ERROR: Still not logged in after login attempt. Aborting.")
            print(page.content()[:2000])
            browser.close()
            sys.exit(1)

        # --- Inspect all session rows that have a delete link ---
        all_rows_with_delete = page.locator("tr:has(a[href*='delete='])")
        row_count = all_rows_with_delete.count()
        print(f"Found {row_count} session row(s) with a delete link.")

        if row_count == 0:
            print("No deletable session rows found. Nothing to log out this run.")
            context.storage_state(path=STATE_FILE)
            browser.close()
            return

        # Debug visibility: print each row's visible text so we can confirm
        # sort order / current-session wording on the first real run.
        other_rows = []
        for i in range(row_count):
            row = all_rows_with_delete.nth(i)
            text = row.inner_text().replace("\n", " | ")
            is_current = any(marker in text for marker in CURRENT_SESSION_MARKERS)
            print(f"  Row {i}: current={is_current} | text=\"{text[:150]}\"")
            if not is_current:
                other_rows.append(row)

        if len(other_rows) == 0:
            print("Only the current (persistent) session was found. Nothing else to log out.")
            context.storage_state(path=STATE_FILE)
            browser.close()
            return

        # ASSUMPTION: the sessions table lists rows newest-first, so the first
        # "other" row is the newest non-persistent session. If this turns out
        # to be wrong once you see real log output, tell me the row order and
        # I'll adjust this to sort by parsed timestamp instead.
        target_row = other_rows[0]
        target_text = target_row.inner_text().replace("\n", " | ")
        print(f"Targeting this session to log out: \"{target_text[:150]}\"")

        delete_link = target_row.locator("a[href*='delete=']").first
        href = delete_link.get_attribute("href")
        print(f"Delete link: {href}")

        page.goto(href, wait_until="networkidle")
        print(f"Logout request sent for target session. Final URL: {page.url}")

        # --- Save (possibly refreshed) session state for the next run ---
        context.storage_state(path=STATE_FILE)
        print(f"Saved session state to {STATE_FILE} for next run.")

        browser.close()


if __name__ == "__main__":
    main()
