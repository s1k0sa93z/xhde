"""
Logs into the Mind Recalls Moodle portal and immediately logs back out.
Intended to be run on a schedule (e.g. every 5 minutes via GitHub Actions)
so that each run performs one login+logout cycle.

Credentials are read from environment variables:
  MINDRECALLS_USERNAME
  MINDRECALLS_PASSWORD

Usage:
  python relogin.py
"""

import os
import sys
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://portal.mindrecalls.com/login/index.php"
BASE_URL = "https://portal.mindrecalls.com"


def main():
    username = os.environ.get("MINDRECALLS_USERNAME")
    password = os.environ.get("MINDRECALLS_PASSWORD")

    if not username or not password:
        print("ERROR: MINDRECALLS_USERNAME and MINDRECALLS_PASSWORD must be set as env vars.")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # --- 1. Load login page ---
        page.goto(LOGIN_URL, wait_until="networkidle")

        # --- 2. Fill in credentials and submit ---
        page.fill("#username", username)
        page.fill("#password", password)
        page.click("#loginbtn")

        # Wait for navigation away from the login page
        page.wait_for_load_state("networkidle")

        current_url = page.url
        if "login/index.php" in current_url:
            # Still on login page -> login likely failed
            print("ERROR: Login appears to have failed. Check credentials or page structure.")
            # Optional: dump page content for debugging in Actions logs
            print(page.content()[:2000])
            browser.close()
            sys.exit(1)

        print(f"Logged in successfully. Landed on: {current_url}")

        # --- 3. Extract sesskey (Moodle embeds it in page JS config) ---
        sesskey = page.evaluate(
            "() => (window.M && M.cfg && M.cfg.sesskey) ? M.cfg.sesskey : null"
        )

        if not sesskey:
            print("ERROR: Could not find sesskey on the page after login.")
            browser.close()
            sys.exit(1)

        print(f"Found sesskey: {sesskey[:4]}... (truncated)")

        # --- 4. Log out using the sesskey ---
        logout_url = f"{BASE_URL}/login/logout.php?sesskey={sesskey}"
        page.goto(logout_url, wait_until="networkidle")

        print(f"Logout request sent. Final URL: {page.url}")

        browser.close()


if __name__ == "__main__":
    main()
