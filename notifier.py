import json, time
from pathlib import Path
from plyer import notification

STATE = Path("user_state.json")
sent = {}

def notify(msg):
    notification.notify(title="Bond Alert", message=msg, timeout=5)

while True:
    if STATE.exists():
        s=json.load(open(STATE))
        for k,v in s["alerts"].items():
            if v["last_status"]=="HIT" and sent.get(k)!="HIT":
                notify(f"{k} HIT @ {v['target']}")
                sent[k]="HIT"
    time.sleep(5)
