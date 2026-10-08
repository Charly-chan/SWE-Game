#!/usr/bin/env python3


from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent
SECTION_RE = re.compile(r"^(#{2,3})\s+(.*?)\s*$")
MECHANIC_HEADINGS = ("mechanics", "controls and mechanics", "core mechanics")
QUESTIONS = (
    ("motion_legible", "Is motion legible: does the player character visibly move and act across the tiles?"),
    ("action_feedback", "Does each player action produce visible feedback (projectile, animation, flash, UI change)?"),
    ("progression", "Does the game visibly progress from earlier to later tiles (new areas, waves, scores, levels)?"),
    ("ending_shown", "Is a recognisable ending screen shown in the final tiles?"),
)


def _heading_title(text: str) -> str:

    title = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s*", "", text.strip())
    return title.strip().strip("*`").lower()


def mechanics_from_gdd(text: str) -> list[str]:

    found: list[str] = []
    in_section = False
    section_level = 0
    table_rows_seen = 0
    for raw in text.splitlines():
        heading = SECTION_RE.match(raw.strip())
        if heading:
            level = len(heading.group(1))
            title = _heading_title(heading.group(2))
            if title in MECHANIC_HEADINGS:
                in_section, section_level, table_rows_seen = True, level, 0
                continue
            if in_section and level <= section_level:
                in_section = False
            elif in_section and level > section_level:
                found.append(heading.group(2).strip().strip("*`"))
            continue
        if not in_section:
            continue
        stripped = raw.strip()
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            table_rows_seen += 1
            if table_rows_seen == 1 or not cells or re.fullmatch(r"[:\- ]*", cells[0]):
                continue
            found.append(cells[0].strip("*`")[:80])
            continue
        bullet = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.*)$", raw)
        if bullet:
            item = bullet.group(1).strip()
            bold = re.match(r"^\*\*(.+?)\*\*", item) or re.match(r"^`(.+?)`", item)
            found.append(bold.group(1) if bold else item.split(":")[0].split(" — ")[0][:80])
    seen: set[str] = set()
    unique: list[str] = []
    for name in found:
        key = name.lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(name)
    return unique


def render_checklist(mechanics: list[str]) -> str:
    template = (KIT_DIR / "watch.md").read_text(encoding="utf-8")
    rows = "\n".join(f"- [ ] Mechanic: {name} — visible in tile(s) ____ ?" for name in mechanics)
    if not rows:
        rows = (
            "- [ ] (no `Mechanics` section found in the GDD; list each mechanic here by hand)"
        )
    return re.sub(
        r"(?:- \[ \] Mechanic: ________ — visible in tile\(s\) ____ \?\n)+",
        rows + "\n",
        template,
        count=1,
    )


def vlm_review(sheet: Path, mechanics: list[str]) -> int:
    key = os.environ.get("PLAYTEST_VLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        print("WATCH_VLM skipped: no PLAYTEST_VLM_API_KEY / OPENAI_API_KEY in this environment "
              "(the matrix sandbox has none). Answer watch.md yourself from the sheet.")
        return 0
    if not sheet.is_file():
        print(f"WATCH_VLM skipped: no contact sheet at {sheet}; run playtest/film.sh first.")
        return 0
    base_url = os.environ.get("PLAYTEST_VLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("PLAYTEST_VLM_MODEL", "gpt-4o-mini")
    image = base64.b64encode(sheet.read_bytes()).decode("ascii")
    questions = "\n".join(f"- {key}: {text}" for key, text in QUESTIONS)
    mechanic_lines = "\n".join(f"- {name}" for name in mechanics) or "- (none listed)"
    prompt = (
        "This is a contact sheet of 24 evenly spaced frames from a replay of a small game, "
        "read left to right, top to bottom. Answer strictly as JSON with keys "
        + ", ".join(k for k, _ in QUESTIONS)
        + " (each an object {\"answer\": \"yes|no|unclear\", \"evidence\": \"<one sentence naming tiles>\"}) "
        "and \"mechanics\" (an object mapping each mechanic name below to the same shape).\n\n"
        f"Questions:\n{questions}\n\nMechanics the designer claims exist:\n{mechanic_lines}\n"
    )
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}},
            ],
        }],
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"WATCH_VLM failed: {exc}")
        return 1
    content = ""
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        print(f"WATCH_VLM unexpected response: {json.dumps(body)[:500]}")
        return 1
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        print("WATCH_VLM answer (not JSON):\n" + content)
        return 0
    try:
        answers = json.loads(match.group(0))
    except json.JSONDecodeError:
        print("WATCH_VLM answer (not JSON):\n" + content)
        return 0
    for key, _ in QUESTIONS:
        row = answers.get(key) or {}
        print(f"WATCH_VLM {key}={row.get('answer', '?')} :: {row.get('evidence', '')}")
    for name, row in (answers.get("mechanics") or {}).items():
        row = row or {}
        print(f"WATCH_VLM mechanic[{name}]={row.get('answer', '?')} :: {row.get('evidence', '')}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gdd", required=True, help="your GDD.md")
    parser.add_argument("--sheet", default=os.environ.get("PLAYTEST_OUT", "playtest_out") + "/sheet.png")
    parser.add_argument("--out", default=os.environ.get("PLAYTEST_OUT", "playtest_out") + "/watch_checklist.md")
    parser.add_argument("--vlm", action="store_true", help="also ask a vision model (off by default; needs a key)")
    args = parser.parse_args(argv[1:])
    gdd = Path(args.gdd)
    if not gdd.is_file():
        print(f"watch.py: GDD not found: {gdd}", file=sys.stderr)
        return 2
    mechanics = mechanics_from_gdd(gdd.read_text(encoding="utf-8", errors="replace"))
    checklist = render_checklist(mechanics)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(checklist, encoding="utf-8")
    print(f"WATCH_MECHANICS count={len(mechanics)} " + " | ".join(mechanics))
    print(f"WATCH_CHECKLIST written to {out}")
    if args.vlm:
        return vlm_review(Path(args.sheet), mechanics)
    print(checklist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
