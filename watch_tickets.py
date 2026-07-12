"""
World Cup Ticket Watcher — Vivid Seats + Gametime + SeatPick
----------------------------------------------------------------
For each event, checks three marketplaces for the cheapest listing
with 2 seats together. Sends one combined Telegram message per run
showing every platform's price, or a loud alert if any price drops
to/below that event's threshold.

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
# CONFIG
# ---------------------------------------------------------------------------
TICKETS_NEEDED = 2
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
CLOSE_RANGE = 200  # within this much above threshold = "getting close" icon


def status_icon(price: int, threshold: int) -> str:
    diff = price - threshold
    if diff <= CLOSE_RANGE:
        return "👀"
    return "⚪"


EVENTS = [
    {
        "label": "Argentina vs England — Atlanta (Jul 15)",
        "threshold": 1500,
        "platforms": [
            {
                "name": "Vivid Seats",
                "url": "https://www.vividseats.com/world-cup-soccer-tickets-mercedes-benz-stadium-7-15-2026--sports-soccer/production/5080871",
                "scroll": True,
            },
            {
                "name": "Gametime",
                "url": "https://gametime.co/fifa/fifa-world-cup-nor-eng-vs-arg-sui-match-102-semi-final-tickets/7-15-2026-atlanta-ga-mercedes-benz-stadium/events/66a7e8a5218fbd1123388be7",
                "scroll": False,
            },
            {
                "name": "SeatPick",
                "url": "https://seatpick.com/tbd-vs-tbd-football-world-cup-semi-finals-tickets/event/321774?quantity=2",
                "scroll": False,
            },
        ],
    },
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# --- Platform-specific price extractors ---

VIVIDSEATS_RE = re.compile(
    r"\|\s*(\d+)(?:[\u2013\-](\d+))?\s*tickets?.*?Fees Incl\.\s*\n\$([\d,]+)\s*\n\s*ea",
    re.S,
)
GAMETIME_RE = re.compile(r"Includes Fees\n\$([\d,]+)/ea")
SEATPICK_RE = re.compile(r"\$([\d,]+)\s*\nwith fees")


def extract_vividseats(text: str) -> tuple[int | None, int]:
    prices = []
    for qty_min, qty_max, price_str in VIVIDSEATS_RE.findall(text):
        lo = int(qty_min)
        hi = int(qty_max) if qty_max else lo
        if lo <= TICKETS_NEEDED <= hi:
            prices.append(int(price_str.replace(",", "")))
    return (min(prices) if prices else None), len(prices)


def extract_gametime(text: str) -> tuple[int | None, int]:
    prices = [int(p.replace(",", "")) for p in GAMETIME_RE.findall(text)]
    return (min(prices) if prices else None), len(prices)


def extract_seatpick(text: str) -> tuple[int | None, int]:
    # SeatPick's URL already filters to quantity=2 and "Seated Together"
    prices = [int(p.replace(",", "")) for p in SEATPICK_RE.findall(text)]
    return (min(prices) if prices else None), len(prices)


EXTRACTORS = {
    "Vivid Seats": extract_vividseats,
    "Gametime": extract_gametime,
    "SeatPick": extract_seatpick,
}


# --- Browser + Telegram plumbing ---

def load_page_text(url: str, scroll: bool = False) -> str:
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
            for _ in range(6):
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(700)
        text = page.inner_text("body")
        browser.close()
    return text


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


# --- Core check logic ---

def check_platform(platform: dict) -> dict:
    name = platform["name"]
    extractor = EXTRACTORS[name]
    try:
        text = load_page_text(platform["url"], scroll=platform.get("scroll", False))
        price, count = extractor(text)
        if count == 0:
            print(f"--- {name} DEBUG: body length = {len(text)} chars, "
                  f"empty = {not text.strip()}", file=sys.stderr)
            print(f"--- {name} DEBUG: first 200 chars:\n{text[:200]}", file=sys.stderr)
        return {"name": name, "price": price, "count": count, "error": None, "url": platform["url"]}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "price": None, "count": 0, "error": str(exc), "url": platform["url"]}


def check_event(event: dict) -> dict:
    results = [check_platform(p) for p in event["platforms"]]
    priced = [(r["name"], r["price"], r["url"]) for r in results if r["price"] is not None]
    if priced:
        best_name, best_price, best_url = min(priced, key=lambda c: c[1])
    else:
        best_name, best_price, best_url = None, None, None

    return {
        **event,
        "platform_results": results,
        "best_name": best_name,
        "best_price": best_price,
        "best_url": best_url,
    }


def main():
    now = timestamp()
    results = [check_event(e) for e in EVENTS]

    alerts = [r for r in results if r["best_price"] is not None and r["best_price"] <= r["threshold"]]
    normal = [r for r in results if r not in alerts]

    # --- Alerts: loud, individual message per event ---
    for r in alerts:
        send_telegram(
            "🎉🔥🚨 PRICE DROP — GO GO GO 🚨🔥🎉\n\n"
            f"💰 <b>${r['best_price']:,}</b> per ticket on <b>{r['best_name']}</b> 💰\n"
            f"(your target was ${r['threshold']:,})\n\n"
            f"<b>{r['label']}</b>\n"
            f"Checked {now}\n\n"
            f"👉 {r['best_url']}\n\n"
            "🔥🔥🔥 BUY NOW BEFORE IT'S GONE 🔥🔥🔥"
        )

    # --- Normal: one combined message, per-platform breakdown ---
    if normal:
        lines = [f"Checked {now}"]
        for r in normal:
            lines.append(f"\n<b>{r['label']}</b> (target ${r['threshold']:,})")
            for pr in r["platform_results"]:
                if pr["error"]:
                    lines.append(f"  ❓ {pr['name']}: check failed")
                elif pr["price"] is None:
                    lines.append(f"  ❓ {pr['name']}: no listings found")
                else:
                    icon = status_icon(pr["price"], r["threshold"])
                    lines.append(f"  {icon} {pr['name']}: ${pr['price']:,}")
        send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
