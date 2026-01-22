import json
import time
from pathlib import Path
from plyer import notification

STATE_FILE = Path("user_state.json")
last_sent = {}

def notify(title, message):
    notification.notify(
        title=title,
        message=message,
        timeout=5
    )

while True:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            state = json.load(f)

        alerts = state.get("alerts", {})
        watchlist = state.get("watchlist", [])

        for symbol in watchlist:
            alert = alerts.get(symbol)
            if not alert:
                continue

            status = alert.get("last_status")

            if status == "HIT" and last_sent.get(symbol) != "HIT":
                notify(
                    "🚨 Bond Alert",
                    f"{symbol} HIT"
                )
                last_sent[symbol] = "HIT"

    time.sleep(5)
