"""
World Cup Ticket Watcher — multi-platform (StubHub + Vivid Seats)
-------------------------------------------------------------------
Checks both marketplaces for the cheapest listing that has (at least)
TICKETS_NEEDED seats together, and sends a Telegram message every run.
If either platform's price is at/below THRESHOLD, sends a loud alert.

No login required on either site — both pages checked here are public
listing pages. The watcher never signs in anywhere.

Environment variables required (GitHub Actions secrets):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

import os
import re
import sys
from datetime import datetime, timezone

import requests
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
EVENT_LABEL = "Argentina vs Switzerland - Kansas City (Jul 11)"
THRESHOLD = 1300          # alert loudly if price-per-ticket <= this
TICKETS_NEEDED = 2

STUBHUB_URL = "https://www.stubhub.com/world-cup-kansas-city-tickets-7-11-2026/event/153021616/"
VIVIDSEATS_URL = "https://www.vividseats.com/world-cup-soccer-tickets-geha-field-at-arrowhead-stadium-7-11-2026--sports-soccer/production/5080868"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

STUBHUB_BLOCK_RE = re.compile(
    r"(\d+)\s+tickets together.*?\$([\d,]+)\s*\n\s*incl\. fees", re.S
)
VIVIDSEATS_BLOCK_RE = re.compile(
    r"\|\s*(\d+)(?:[\u2013\-](\d+))?\s*tickets?.*?Fees Incl\.\s*\n\$([\d,]+)\s*\nea",
    re.S,
)


def load_page_text(url: str, scroll: bool = False) -> tuple[str, str]:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(3000)

        if scroll:
            # Listings on some sites are virtualized/lazy-loaded and only
            # render as they enter the viewport. Scroll down in steps so
            # they actually populate before we read the page.
            for _ in range(6):
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(700)

        title = page.title()
        text = page.inner_text("body")
        browser.close()
    return title, text


def cheapest_stubhub(text: str) -> tuple[float | None, int]:
    prices = []
    for qty_str, price_str in STUBHUB_BLOCK_RE.findall(text):
        if int(qty_str) >= TICKETS_NEEDED:
            prices.append(int(price_str.replace(",", "")))
    return (min(prices) if prices else None), len(prices)


def cheapest_vividseats(text: str) -> tuple[float | None, int]:
    prices = []
    for qty_min, qty_max, price_str in VIVIDSEATS_BLOCK_RE.findall(text):
        lo = int(qty_min)
        hi = int(qty_max) if qty_max else lo
        if lo <= TICKETS_NEEDED <= hi:
            prices.append(int(price_str.replace(",", "")))
    return (min(prices) if prices else None), len(prices)


def send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars.", file=sys.stderr)
        sys.exit(1)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
        timeout=15,
    )
    resp.raise_for_status()


def check_platform(name: str, url: str, extractor, scroll: bool = False) -> tuple[str, float | None, int, Exception | None]:
    try:
        title, text = load_page_text(url, scroll=scroll)
        price, count = extractor(text)
        if count == 0:
            has_marker = ("tickets together" in text) or ("Fees Incl." in text) or ("incl. fees" in text)
            print(f"--- {name} DEBUG: page title = {title!r}", file=sys.stderr)
            print(f"--- {name} DEBUG: total body text length = {len(text)} chars", file=sys.stderr)
            print(f"--- {name} DEBUG: pricing marker text found anywhere on page = {has_marker}", file=sys.stderr)
            print(f"--- {name} DEBUG: first 300 chars:\n{text[:300]}", file=sys.stderr)
            print(f"--- {name} DEBUG: last 300 chars:\n{text[-300:]}", file=sys.stderr)
            for marker in ("Fees Incl.", "incl. fees", "tickets together"):
                idx = text.find(marker)
                if idx != -1:
                    start = max(0, idx - 200)
                    end = min(len(text), idx + 100)
                    print(
                        f"--- {name} DEBUG: context around first '{marker}':\n{text[start:end]}",
                        file=sys.stderr,
                    )
            if not text.strip():
                # Empty body from a real ticket site is the classic signature
                # of a bot-detection interstitial that never resolved.
                return name, None, -1, None
        return name, price, count, None
    except Exception as exc:  # noqa: BLE001
        return name, None, 0, exc


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    results = [
        check_platform("StubHub", STUBHUB_URL, cheapest_stubhub),
        check_platform("Vivid Seats", VIVIDSEATS_URL, cheapest_vividseats, scroll=True),
    ]

    lines = [f"<b>{EVENT_LABEL}</b>", f"Checked {now} (need {TICKETS_NEEDED} together)"]
    best_price = None
    best_platform = None
    errors = []

    for name, price, count, err in results:
        if err:
            errors.append(f"{name}: FAILED ({err})")
        elif count == -1:
            lines.append(f"{name}: page appears blocked (empty response) — skipped this run")
        elif price is None:
            lines.append(f"{name}: no {TICKETS_NEEDED}-together listings found ({count} scanned)")
        else:
            lines.append(f"{name}: ${price:,} ea  ({count} matching listings)")
            if best_price is None or price < best_price:
                best_price = price
                best_platform = name

    if errors:
        lines.append("")
        lines.extend(errors)

    if best_price is not None and best_price <= THRESHOLD:
        send_telegram(
            "🚨🚨🚨 PRICE ALERT 🚨🚨🚨\n"
            + f"<b>{best_platform}</b> has 2 together at <b>${best_price:,}</b> "
            + f"(threshold ${THRESHOLD:,})\n\n"
            + "\n".join(lines)
        )
    else:
        send_telegram("✅ " + "\n".join(lines))

    if errors and best_price is None:
        # both platforms failed — non-zero exit so the Actions run shows red
        sys.exit(1)


if __name__ == "__main__":
    main()
