"""Compact numeric evidence from the existing unflagged input replay."""
import hashlib
import json
import math
from pathlib import Path

SCHEMA = "swe-game.numeric-replay.v1"


def summarize_numeric_rows(rows):
    slots = {}
    for row in rows:
        level, frame, numeric = row.get("lv"), row.get("f"), row.get("n")
        if type(level) is not int or type(frame) is not int or not isinstance(numeric, dict):
            continue
        for slot, value in numeric.items():
            if type(value) not in {int, float} or not math.isfinite(value):
                continue
            reading = slots.setdefault((level, slot), {
                "level": level, "slot": slot, "first": value, "last": value,
                "min": value, "max": value, "valid_frames": 0,
                "first_frame": frame, "last_frame": -1,
            })
            if frame <= reading["last_frame"]:
                continue
            reading["last"] = value
            reading["min"] = min(reading["min"], value)
            reading["max"] = max(reading["max"], value)
            reading["valid_frames"] += 1
            reading["last_frame"] = frame
    return [slots[key] for key in sorted(slots)]


def read_numeric_replay(path: Path, *, usable: bool):
    result = {"schema": SCHEMA, "status": "unusable_replay", "slots": []}
    if not usable:
        return result
    try:
        data = path.read_bytes()
        rows = json.loads(data)["rows"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("invalid replay rows")
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {**result, "status": "missing_trace", "detail": str(error)}
    return {
        **result, "status": "recorded", "sha256": hashlib.sha256(data).hexdigest(),
        "frames": len(rows), "slots": summarize_numeric_rows(rows),
    }
