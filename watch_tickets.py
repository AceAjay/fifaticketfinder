"""
World Cup Ticket Watcher — Vivid Seats, multi-event
------------------------------------------------------
Checks Vivid Seats hourly for each event in EVENTS below, for the cheapest
listing with 2 seats together. Sends one combined Telegram message per run:
a clean line per event normally, or a big attention-grabbing alert if any
event's price drops to/below its own threshold.

StubHub is intentionally not checked here — GitHub Actions runs from cloud
IPs that StubHub blocks outright. StubHub tracking, if wanted, runs
separately from your own Mac via check_stubhub_local.py.

Environment variables required (GitHub Actions secrets):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# CONFIG — add/remove events here
# ---------------------------------------------------------------------------
TICKETS_NEEDED = 2
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

EVENTS = [
    {
        "label": "Argentina vs Switzerland — Kansas City (Jul 11)",
        "url": "https://www.vividseats.com/world-cup-soccer-tickets-geha-field-at-arrowhead-stadium-7-11-2026--sports-soccer/production/5080868",
        "threshold": 1300,
    },
    {
        "label": "World Cup Semi-Final — Atlanta (Jul 15)",
        "url": "https://www.vividseats.com/world-cup-soccer-tickets-mercedes-benz-stadium-7-15-2026--sports-soccer/production/5080871",
        "threshold": 1500,
    },
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

VIVIDSEATS_BLOCK_RE = re.compile(
    r"\|\s*(\d+)(?:[\u2013\-](\d+))?\s*tickets?.*?Fees Incl\.\s*\n\$([\d,]+)\s*\n\s*ea",
    re.S,
)


def load_page_text(url: str) -> str:
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
        for _ in range(6):
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(700)
        text = page.inner_text("body")
        browser.close()
    return text


def cheapest_vividseats(text: str) -> tuple[int | None, int]:
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


def timestamp() -> str:
    return datetime.now(LOCAL_TZ).strftime("%a %I:%M %p PT")


def check_event(event: dict) -> dict:
    try:
        text = load_page_text(event["url"])
        price, count = cheapest_vividseats(text)
        return {**event, "price": price, "count": count, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {**event, "price": None, "count": 0, "error": str(exc)}


def main():
    now = timestamp()
    results = [check_event(e) for e in EVENTS]

    alerts = [r for r in results if r["price"] is not None and r["price"] <= r["threshold"]]
    normal = [r for r in results if r not in alerts]

    # --- Alerts get their own loud message each, sent first ---
    for r in alerts:
        send_telegram(
            "🎉🔥🚨 PRICE DROP — GO GO GO 🚨🔥🎉\n\n"
            f"💰 <b>${r['price']:,}</b> per ticket 💰\n"
            f"(your target was ${r['threshold']:,})\n\n"
            f"<b>{r['label']}</b>\n"
            f"Checked {now}\n\n"
            f"👉 {r['url']}\n\n"
            "🔥🔥🔥 BUY NOW BEFORE IT'S GONE 🔥🔥🔥"
        )

    # --- Everything else: one combined, quiet status message ---
    if normal:
        lines = [f"✅ Checked {now}"]
        for r in normal:
            if r["error"]:
                lines.append(f"{r['label']}: FAILED ({r['error']})")
            elif r["price"] is None:
                lines.append(f"{r['label']}: no {TICKETS_NEEDED}-together listings found")
            else:
                lines.append(f"{r['label']}: ${r['price']:,} (target ${r['threshold']:,})")
        send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()

