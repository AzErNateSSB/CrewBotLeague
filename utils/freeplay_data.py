import json
import os
from typing import Optional

FREEPLAY_ACT_DIR = os.path.join("data", "freeplay_active")


def save_freeplay_active(channel_id: int, data: dict):
    os.makedirs(FREEPLAY_ACT_DIR, exist_ok=True)
    with open(os.path.join(FREEPLAY_ACT_DIR, f"{channel_id}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_freeplay_active(channel_id: int) -> Optional[dict]:
    p = os.path.join(FREEPLAY_ACT_DIR, f"{channel_id}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def del_freeplay_active(channel_id: int):
    p = os.path.join(FREEPLAY_ACT_DIR, f"{channel_id}.json")
    if os.path.exists(p):
        os.remove(p)
