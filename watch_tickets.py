"""
World Cup Ticket Watcher — Vivid Seats + Gametime, multi-event
------------------------------------------------------------------
For each event in EVENTS, checks both Vivid Seats and Gametime for the
cheapest listing with 2 seats together, and reports whichever platform is
cheaper. Sends one combined Telegram message per run for normal status; a
big attention-grabbing alert fires immediately if either platform's price
drops to/below that event's threshold.

StubHub is intentionally not checked here — GitHub Actions runs from cloud
IPs that StubHub blocks outright (confirmed via testing, both cloud and
local). Not worth chasing further.

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
CLOSE_RANGE = 200  # within this much above threshold = "getting close" icon


def status_icon(price: int, threshold: int) -> str:
    diff = price - threshold
    if diff <= CLOSE_RANGE:
        return "👀"  # within $200 above target — worth watching closely
    return "⚪"  # still well above target — quiet/neutral

EVENTS = [
    {
        "label": "Argentina vs Switzerland — Kansas City (Jul 11)",
        "threshold": 1300,
        "vividseats_url": "https://www.vividseats.com/world-cup-soccer-tickets-geha-field-at-arrowhead-stadium-7-11-2026--sports-soccer/production/5080868",
        "gametime_url": "https://gametime.co/fifa/fifa-world-cup-argentina-vs-switzerland-match-100-quarter-final-tickets/7-11-2026-kansas-city-mo-geha-field-at-arrowhead-stadium/events/66ac1f15ba6c613e111c87d3",
    },
    {
        "label": "World Cup Semi-Final — Atlanta (Jul 15)",
        "threshold": 1500,
        "vividseats_url": "https://www.vividseats.com/world-cup-soccer-tickets-mercedes-benz-stadium-7-15-2026--sports-soccer/production/5080871",
        "gametime_url": "https://gametime.co/fifa/fifa-world-cup-nor-eng-vs-arg-sui-match-102-semi-final-tickets/7-15-2026-atlanta-ga-mercedes-benz-stadium/events/66a7e8a5218fbd1123388be7",
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
GAMETIME_RE = re.compile(r"Includes Fees\n\$([\d,]+)/ea")


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


def cheapest_vividseats(text: str) -> tuple[int | None, int]:
    prices = []
    for qty_min, qty_max, price_str in VIVIDSEATS_BLOCK_RE.findall(text):
        lo = int(qty_min)
        hi = int(qty_max) if qty_max else lo
        if lo <= TICKETS_NEEDED <= hi:
            prices.append(int(price_str.replace(",", "")))
    return (min(prices) if prices else None), len(prices)


def cheapest_gametime(text: str) -> tuple[int | None, int]:
    # The event page defaults to a "2 Tickets" filter already, so every
    # /ea price on the page is already for the requested quantity.
    prices = [int(p.replace(",", "")) for p in GAMETIME_RE.findall(text)]
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


def check_platform(url: str, extractor, scroll: bool = False) -> dict:
    try:
        text = load_page_text(url, scroll=scroll)
        price, count = extractor(text)
        return {"price": price, "count": count, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"price": None, "count": 0, "error": str(exc)}


def check_event(event: dict) -> dict:
    vs = check_platform(event["vividseats_url"], cheapest_vividseats, scroll=True)
    gt = check_platform(event["gametime_url"], cheapest_gametime, scroll=False)

    candidates = []
    if vs["price"] is not None:
        candidates.append(("Vivid Seats", vs["price"]))
    if gt["price"] is not None:
        candidates.append(("Gametime", gt["price"]))

    best_platform, best_price = min(candidates, key=lambda c: c[1]) if candidates else (None, None)

    return {
        **event,
        "vividseats": vs,
        "gametime": gt,
        "best_platform": best_platform,
        "best_price": best_price,
    }


def main():
    now = timestamp()
    results = [check_event(e) for e in EVENTS]

    alerts = [r for r in results if r["best_price"] is not None and r["best_price"] <= r["threshold"]]
    normal = [r for r in results if r not in alerts]

    # --- Alerts get their own loud message each, sent first ---
    for r in alerts:
        url = r["vividseats_url"] if r["best_platform"] == "Vivid Seats" else r["gametime_url"]
        send_telegram(
            "🎉🔥🚨 PRICE DROP — GO GO GO 🚨🔥🎉\n\n"
            f"💰 <b>${r['best_price']:,}</b> per ticket on <b>{r['best_platform']}</b> 💰\n"
            f"(your target was ${r['threshold']:,})\n\n"
            f"<b>{r['label']}</b>\n"
            f"Checked {now}\n\n"
            f"👉 {url}\n\n"
            "🔥🔥🔥 BUY NOW BEFORE IT'S GONE 🔥🔥🔥"
        )

    # --- Everything else: one combined, quiet status message ---
    if normal:
        lines = [f"Checked {now}"]
        for r in normal:
            lines.append(f"\n<b>{r['label']}</b> (target ${r['threshold']:,})")

            vs, gt = r["vividseats"], r["gametime"]
            for platform_name, result in (("Vivid Seats", vs), ("Gametime", gt)):
                if result["error"]:
                    lines.append(f"❓ {platform_name}: check failed")
                elif result["price"] is None:
                    lines.append(f"❓ {platform_name}: no listings found")
                else:
                    icon = status_icon(result["price"], r["threshold"])
                    lines.append(f"{icon} {platform_name}: ${result['price']:,}")

        send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
