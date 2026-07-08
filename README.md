# World Cup Ticket Watcher

Polls StubHub AND Vivid Seats hourly, filters to listings with 2 seats
together, tells you the cheapest of the two on every run, and sends a
loud alert if either drops to/below your threshold.

**No login needed, on either site.** Both URLs checked are public listing
pages — the kind anyone gets by searching without an account. The watcher
never enters credentials anywhere. This is actually a feature, not just a
convenience: no session cookie to expire, nothing tied to your account to
get flagged.

**Why not viagogo too?** viagogo is StubHub's own parent company — same
inventory, same US listings, just a different skin. Checking it separately
would double-count the same tickets, not give you a third opinion. If you
want a genuine third source, SeatGeek is the next best candidate — say the
word and I'll add it the same way.

## 1. Create your Telegram bot (~5 min)

1. In Telegram, message **@BotFather** → `/newbot` → follow prompts.
2. BotFather gives you a token like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
   That's your `TELEGRAM_BOT_TOKEN`.
3. Send your new bot any message (e.g. "hi") so it knows you exist.
4. Visit this URL in your browser (swap in your token):
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   Find `"chat":{"id":123456789,...}` in the response — that number is your
   `TELEGRAM_CHAT_ID`.

## 2. Push this folder to a GitHub repo

```
git init
git add .
git commit -m "world cup ticket watcher"
git remote add origin <your repo url>
git push -u origin main
```

## 3. Add secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 4. That's it

The workflow in `.github/workflows/watch.yml` runs every hour automatically.
You can also trigger it manually from the **Actions** tab (`Run workflow`)
to test it right away without waiting for the next hour.

## Tuning

- **Match/threshold**: edit `EVENT_URL`, `EVENT_LABEL`, `THRESHOLD` at the
  top of `watch_tickets.py`.
- **Cadence**: edit the `cron` line in `watch.yml`. `"0 * * * *"` = hourly.
  `"*/30 * * * *"` = every 30 min. Don't go much tighter than 15 min —
  raises the odds of getting rate-limited or CAPTCHA'd.
- **Multiple matches**: duplicate the script (e.g. `watch_tickets_semis.py`)
  with a different `EVENT_URL`/`THRESHOLD`, and add a second step or job
  in the workflow.

## Known limitations (read this before trusting it blindly)

- StubHub can change page structure or add bot-detection at any time —
  if the watcher goes quiet or errors, that's the first thing to check.
- This reads publicly visible listings, not a private account view — it
  doesn't need your login, which is actually safer (no session/cookie to
  leak or expire).
- This only **notifies** — it does not purchase anything. Buying is
  still a manual click from you, by design.
