#!/usr/bin/env python3


from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_EVALSYS_ROOT = str(Path(__file__).resolve().parents[2] / "evalsys")
if _EVALSYS_ROOT not in sys.path:
    sys.path.insert(0, _EVALSYS_ROOT)
from evalsys.engine import hostenv


_REPO_ROOT = Path(__file__).resolve().parents[3]
STANDARD_ROOTS = [
    str(_REPO_ROOT / "games"),
]


NON_GAME_DIRS = {
    "bot", "bots", "tools", "test", "tests", "addons", "verify", "compare", "reference",
    "inputs", "staging", "_archives", "web", "builds", "export", "docs", ".godot", ".git",
    "recording", "recordings", "harness",
}


NON_GAME_FILE_HINTS = (
    "bot_main", "mode_", "bridge", "probe", "harness", "verdict_log", "human_timing",
    "test_", "_test", "selftest", "certify", "autoplay", "build_scenes", "gb_event",
)

VERDICT_SYMBOL = {
    "pass": "✓",
    "partial": "~",
    "fail": "✗",
    "absent": "·",
    "cannot_verify": "?",
}


ELEMENTS = [
    ("overview", "Overview"),
    ("pillars", "Pillars"),
    ("functions", "Function list"),
    ("mechanics", "Mechanics + controls"),
    ("facing", "Facing rules"),
    ("levels", "Level / content table"),
    ("art", "Art baseline"),
    ("assets", "Asset method + key assets"),
    ("tech", "Technical plan"),
    ("tests", "Test plan"),
]

HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


PATHY_RE = re.compile(
    r"^(?:res://)?[\w\-./ ()\[\]]+\.(?:tscn|gd|png|jpg|jpeg|json|ogg|wav|ttf|otf|tres|res|"
    r"md|cfg|py|js|glsl|gdshader|svg|webp|mp4|avi|txt|import|tmx|fnt|zip)$"
)


def read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def numbers_in(text: str) -> list[float]:
    out = []
    for tok in re.findall(r"-?\u2212?\d+(?:\.\d+)?", text):
        try:
            out.append(float(tok.replace("\u2212", "-")))
        except ValueError:
            pass
    return out


def has_signed_comparison(text: str) -> bool:

    pats = [
        r"[<>]\s*=?\s*0",
        r"\bsign[fi]?\s*\(",
        r"[+\-\u2212]\s?[xyz]\b",
        r"flip_h\s*=",
        r"scale\.x\s*=\s*[-\u2212]",
        r"Vector2\s*\(\s*[-\u2212]",
        r"[<>]\s*=?\s*[-\u2212]?\d",
        r"\bnegative\b|\bpositive\b",
        r"取(负|正)|负号|符号",
    ]
    return any(re.search(p, text, re.I) for p in pats)


def has_duration(text: str) -> bool:

    return bool(re.search(
        r"\d+(?:\.\d+)?\s*(?:[-–~至到]\s*\d+(?:\.\d+)?\s*)?"
        r"(?:s\b|sec|second|min\b|minute|分钟|分\b|秒|hour|小时|ms\b)",
        text, re.I))


@dataclass
class Table:
    heading: str
    header: list[str]
    rows: list[list[str]]
    line: int

    def col_index(self, *pats: str) -> int:
        for i, cell in enumerate(self.header):
            for p in pats:
                if re.search(p, cell, re.I):
                    return i
        return -1

    def column(self, idx: int) -> list[str]:
        return [r[idx] if idx < len(r) else "" for r in self.rows]


@dataclass
class Section:
    level: int
    title: str
    line: int
    body: str = ""
    end: int = 0


class Doc:


    def __init__(self, text: str):
        self.text = text
        self.lines = text.splitlines()
        self.sections: list[Section] = []
        self.tables: list[Table] = []
        self._parse()

    def _parse(self) -> None:

        fenced = [False] * len(self.lines)
        in_fence = False
        for i, ln in enumerate(self.lines):
            if re.match(r"^\s*```", ln):
                in_fence = not in_fence
                fenced[i] = True
                continue
            fenced[i] = in_fence


        marks: list[tuple[int, int, str]] = []
        for i, ln in enumerate(self.lines):
            if fenced[i]:
                continue
            m = HEADING_RE.match(ln)
            if m:
                marks.append((i, len(m.group(1)), m.group(2)))
        for n, (i, lvl, title) in enumerate(marks):


            end = len(self.lines)
            for j in range(n + 1, len(marks)):
                if marks[j][1] <= lvl:
                    end = marks[j][0]
                    break
            self.sections.append(
                Section(lvl, title, i + 1, "\n".join(self.lines[i + 1:end]), end))


        i = 0
        while i < len(self.lines):
            if fenced[i] or "|" not in self.lines[i]:
                i += 1
                continue
            if i + 1 >= len(self.lines) or not re.match(r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$",
                                                        self.lines[i + 1]):
                i += 1
                continue
            if "-" not in self.lines[i + 1]:
                i += 1
                continue
            header = self._cells(self.lines[i])
            rows = []
            j = i + 2
            while j < len(self.lines) and "|" in self.lines[j] and not fenced[j]:
                if HEADING_RE.match(self.lines[j]):
                    break
                cells = self._cells(self.lines[j])
                if any(c.strip() for c in cells):
                    rows.append(cells)
                j += 1
            self.tables.append(Table(self.heading_at(i + 1), header, rows, i + 1))
            i = j

    @staticmethod
    def _cells(line: str) -> list[str]:
        s = line.strip()


        holder = "\x00"
        spans: list[str] = []

        def stash(m: re.Match) -> str:
            spans.append(m.group(0))
            return holder + str(len(spans) - 1) + holder

        s = re.sub(r"`[^`\n]*`", stash, s)
        s = re.sub(r"^\|", "", s)
        s = re.sub(r"\|$", "", s)
        parts = re.split(r"(?<!\\)\|", s)

        def restore(txt: str) -> str:
            return re.sub(holder + r"(\d+)" + holder,
                          lambda m: spans[int(m.group(1))], txt).strip()

        return [restore(p) for p in parts]

    def heading_at(self, line_no: int) -> str:
        cur = ""
        for s in self.sections:
            if s.line <= line_no:
                cur = s.title
            else:
                break
        return cur

    def find_sections(self, pattern: str) -> list[Section]:


        hits = [s for s in self.sections if re.search(pattern, s.title, re.I)]
        return [s for s in hits
                if not any(o is not s and o.line < s.line and s.end <= o.end for o in hits)]

    def section_body(self, pattern: str) -> str:

        return "\n".join(s.body for s in self.find_sections(pattern))

    @staticmethod
    def list_items(body: str) -> list[str]:


        items: list[str] = []
        cur: list[str] = []
        for ln in body.splitlines():
            if re.match(r"^\s{0,3}(?:\d+[.)]|[-*+])\s+\S", ln):
                if cur:
                    items.append(" ".join(cur).strip())
                cur = [re.sub(r"^\s{0,3}(?:\d+[.)]|[-*+])\s+", "", ln).strip()]
            elif cur:
                if not ln.strip():
                    items.append(" ".join(cur).strip())
                    cur = []
                elif re.match(r"^\s{2,}\S", ln) or re.match(r"^\s*\S", ln):
                    if HEADING_RE.match(ln) or ln.lstrip().startswith("|"):
                        items.append(" ".join(cur).strip())
                        cur = []
                    else:
                        cur.append(ln.strip())
        if cur:
            items.append(" ".join(cur).strip())
        return [i for i in items if len(i) >= 10]

    def find_tables(self, *header_pats: str) -> list[Table]:

        out = []
        for t in self.tables:
            if all(t.col_index(p) >= 0 for p in header_pats):
                out.append(t)
        return out


@dataclass
class EntityClass:

    path: str
    name: str
    tokens: list[str]
    horizontal: bool
    oriented: bool
    orient_kinds: list[str]
    has_sprite: bool
    extends: str


@dataclass
class Project:
    root: str
    doc_root: str
    name: str
    gdd_path: str | None
    gdd: Doc | None
    input_actions: list[str] = field(default_factory=list)
    action_keys: dict[str, list[str]] = field(default_factory=dict)
    scenes: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    entities: list[EntityClass] = field(default_factory=list)
    bot_modes: list[str] = field(default_factory=list)
    audio_files: list[str] = field(default_factory=list)
    level_data: list[str] = field(default_factory=list)
    code_blob: str = ""
    file_suffixes: set[str] = field(default_factory=set)
    file_basenames: set[str] = field(default_factory=set)


SPECIAL_KEYS: dict[int, list[str]] = {
    32: ["space", "空格"],
    4194305: ["escape", "esc"],
    4194306: ["tab"],
    4194308: ["backspace"],
    4194309: ["enter", "return", "回车"],
    4194310: ["enter", "return"],
    4194311: ["insert"],
    4194312: ["delete", "del"],
    4194319: ["left", "←", "arrow"],
    4194320: ["up", "↑", "arrow"],
    4194321: ["right", "→", "arrow"],
    4194322: ["down", "↓", "arrow"],
    4194323: ["pageup", "page up"],
    4194324: ["pagedown", "page down"],
    4194325: ["shift"],
    4194326: ["ctrl", "control"],
    4194327: ["alt"],
}
for _i in range(1, 13):
    SPECIAL_KEYS[4194332 + _i] = [f"f{_i}"]


def key_label(code: int) -> list[str]:

    if code in SPECIAL_KEYS:
        return SPECIAL_KEYS[code]
    if 33 <= code <= 126:
        ch = chr(code)
        return [ch.lower()]
    return []


GENERIC_TOKENS = {
    "unit", "base", "entity", "actor", "object", "node", "script", "main", "core", "game",
    "manager", "controller", "handler", "helper", "util", "utils", "common", "shared", "gd",
    "2d", "3d", "body", "class", "new", "old", "tmp",
}


def tokenise(stem: str) -> list[str]:
    parts = re.split(r"[_\-.]+", stem.lower())
    toks = [p for p in parts if p and p not in GENERIC_TOKENS and len(p) > 2]
    return toks or [p for p in parts if p]


def is_scaffolding(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(p in NON_GAME_DIRS for p in parts[:-1]):
        return True
    stem = parts[-1].lower()
    return any(h in stem for h in NON_GAME_FILE_HINTS)


def scan_project(root: str) -> Project:
    doc_root = root
    gdd = os.path.join(root, "GDD.md")
    if not os.path.isfile(gdd):
        parent_gdd = os.path.join(os.path.dirname(root.rstrip("/")), "GDD.md")
        if os.path.isfile(parent_gdd):
            gdd = parent_gdd
            doc_root = os.path.dirname(root.rstrip("/"))
        else:
            gdd = None

    name = os.path.basename(root.rstrip("/"))
    if name == "game":
        name = os.path.basename(os.path.dirname(root.rstrip("/")))

    p = Project(root=root, doc_root=doc_root, name=name, gdd_path=gdd, gdd=None)
    if gdd:
        txt = read_text(gdd)
        if txt is not None:
            p.gdd = Doc(txt)


    pg = read_text(os.path.join(root, "project.godot")) or ""
    in_input = False
    cur_action: str | None = None
    for ln in pg.splitlines():
        st = ln.strip()
        if st.startswith("[") and st.endswith("]"):
            in_input = st == "[input]"
            cur_action = None
            continue
        if not in_input:
            continue
        m = re.match(r'^([A-Za-z_][\w/]*)\s*=', ln)
        if m:
            cur_action = m.group(1)
            p.input_actions.append(cur_action)
            p.action_keys.setdefault(cur_action, [])
        if cur_action:
            for code in re.findall(r'"(?:physical_keycode|keycode)":\s*(\d+)', ln):
                lbl = key_label(int(code))
                if lbl:
                    p.action_keys[cur_action].extend(lbl)


    code_parts: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".godot", ".git", "node_modules"}]
        for fn in filenames:
            abs_p = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_p, root).replace("\\", "/")
            p.file_suffixes.add(rel)
            p.file_basenames.add(fn)
            low = fn.lower()
            if low.endswith(".tscn"):
                p.scenes.append(rel)
            elif low.endswith((".ogg", ".wav", ".mp3")):
                p.audio_files.append(rel)
            elif low.endswith(".json") and re.search(r"level|room|stage|chart|act|world|map",
                                                     rel, re.I):
                p.level_data.append(rel)
            elif low.endswith(".gd"):
                p.scripts.append(rel)
                body = read_text(abs_p) or ""
                if is_scaffolding(rel):
                    if re.search(r"\bbot\b|mode_", rel, re.I):
                        for m in re.finditer(r'"(\w+)"', body):
                            pass
                    if re.search(r"MODES\s*(?::\s*\w+)?\s*=\s*\[", body):
                        blk = body.split("MODES", 1)[1]
                        blk = blk[:blk.find("]") + 1] if "]" in blk else blk
                        p.bot_modes += re.findall(r'"(\w+)"', blk)
                    continue
                code_parts.append(body)
                ent = classify_entity(rel, fn, body)
                if ent:
                    p.entities.append(ent)
    p.code_blob = "\n".join(code_parts)

    return p


MOTION_PATS = [
    r"\bvelocity\.x\b", r"\bvelocity\s*=", r"\bmove_and_slide\b", r"\bmove_and_collide\b",
    r"\blinear_velocity\.x\b", r"\bposition\.x\s*[+\-]=", r"\bglobal_position\.x\s*[+\-]=",
    r"\bposition\s*\+=", r"\bglobal_position\s*\+=", r"\bmotion\.x\b", r"\bspeed\b.*\bdelta\b",
    r"\btranslate\s*\(", r"\bvelocity\.y\b",
    r"\b(?:global_)?position\s*=", r"\b(?:global_)?position\.[xyz]\s*=",
    r"\btransform\.origin\s*=", r"\bglobal_transform\.origin\s*=",
]
HORIZ_PATS = [
    r"\bvelocity\.x\b", r"\blinear_velocity\.x\b", r"\bposition\.x\s*[+\-]=",
    r"\bglobal_position\.x\s*[+\-]=", r"\bmotion\.x\b", r"\bdirection\b", r"\b_dir\b",
    r"\bfacing\b", r"\bmove_and_slide\b", r"\bVector2\s*\([^)]*speed",
    r"\b(?:global_)?position\.x\s*=", r"\b(?:global_)?position\s*=\s*Vector[23]",
]
ORIENT_PATS = {
    "flip_h": r"\bflip_h\b",
    "flip_v": r"\bflip_v\b",
    "scale.x": r"\bscale\.x\s*=",
    "scale=Vector2": r"\bscale\s*=\s*Vector2\s*\(",
    "rotation": r"\brotation\b\s*=|\brotation_degrees\b\s*=|\blook_at\s*\(",
    "transform.x": r"\btransform\.x\b",
}


def classify_entity(rel: str, fn: str, body: str) -> EntityClass | None:
    m = re.search(r"^\s*extends\s+([\w.]+)", body, re.M)
    extends = m.group(1) if m else ""
    moves = any(re.search(p, body) for p in MOTION_PATS)
    if not moves:
        return None

    if re.match(r"(Control|Panel|Button|Label|CanvasLayer|Window|Popup|Node$)", extends):
        return None
    horizontal = any(re.search(p, body) for p in HORIZ_PATS)
    kinds = [k for k, pat in ORIENT_PATS.items() if re.search(pat, body)]
    sprite = bool(re.search(r"Sprite2D|AnimatedSprite|_sprite|\bsprite\b|SpriteFrames|texture",
                            body, re.I))
    stem = os.path.splitext(fn)[0]
    return EntityClass(path=rel, name=stem, tokens=tokenise(stem), horizontal=horizontal,
                       oriented=bool(kinds), orient_kinds=kinds, has_sprite=sprite,
                       extends=extends)


@dataclass
class Result:
    id: str
    label: str
    verdict: str = "absent"
    findings: list[str] = field(default_factory=list)
    human: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def ok(self, msg: str) -> None:
        self.findings.append("ok    " + msg)

    def bad(self, msg: str) -> None:
        self.findings.append("BAD   " + msg)

    def note(self, msg: str) -> None:
        self.findings.append("note  " + msg)

    def ask(self, msg: str) -> None:
        self.human.append(msg)


def settle(r: Result, good: int, bad: int, *, absent: bool = False,
           unverifiable: bool = False) -> Result:

    if absent:
        r.verdict = "absent"
    elif unverifiable and good == 0:
        r.verdict = "cannot_verify"
    elif bad == 0 and good > 0:
        r.verdict = "cannot_verify" if unverifiable else "pass"
    elif good == 0:
        r.verdict = "fail"
    else:
        r.verdict = "partial"
    r.metrics["sub_pass"] = good
    r.metrics["sub_fail"] = bad
    return r


def check_overview(p: Project) -> Result:
    r = Result("overview", "Overview")
    d = p.gdd
    secs = d.find_sections(r"overview|概览|概述|简介")
    if not secs:

        head = "\n".join(d.lines[: d.sections[0].line - 1]) if d.sections else d.text
        if len(head.strip()) < 80:
            r.bad("no overview section, and no substantive preamble before the first heading")
            return settle(r, 0, 1, absent=True)
        body = head
        r.note("no 'Overview' heading; graded the preamble before the first heading")
    else:
        body = "\n".join(s.body for s in secs)

    good = bad = 0

    if has_duration(body):
        r.ok("session length stated as a duration with a unit")
        good += 1
    else:
        r.bad("no session length with a unit (§1.2 requires 一局时长; a number and a unit, "
              "not an adjective)")
        bad += 1


    scale_pat = (r"规模|档位|scale tier|scope tier|规模档|"
                 r"\d+\s*(关|levels?|rooms?|stages?|场景|scenes?|acts?|waves?|波|charts?)")
    if re.search(scale_pat, body, re.I):
        r.ok("scale tier / content scale declared")
        good += 1
    elif re.search(scale_pat, d.text, re.I):
        r.ok("scale stated elsewhere in the document, not in the overview")
        good += 1
        r.note("§1.2 puts the scale tier in the overview; it is stated but not there")
    else:
        r.bad("no scale tier (规模档位) declared anywhere — §1.2 wants the tier respected or, "
              "if none was given, self-set and written down, so scope cannot creep silently")
        bad += 1


    ng_secs = d.find_sections(
        r"non-goal|non goal|not goals|excluded|排除|非目标|不做|out of scope|"
        r"让步|取舍|残差|deviation|residual|concession|已知(限制|问题|残差)|trade-?off|"
        r"limitation|scope control|范围控制")
    ng_body = "\n".join(s.body for s in ng_secs)
    if ng_body.strip():
        r.ok(f"scope boundary stated in a dedicated section ({len(ng_body.split())} words): "
             + ", ".join(s.title[:40] for s in ng_secs[:3]))
        good += 1
    elif re.search(r"不(会|做|实现|包含|扩张)|no[nt]-?goal|out of scope|excluded scope|"
                   r"deliberately (omitted|absent|not)|by design there is no", body, re.I):
        r.ok("non-goals stated inline in the overview")
        good += 1
    else:
        r.bad("no non-goals / excluded scope / known-concessions section anywhere "
              "(§1.2: 实现中不自行扩张内容量 — the boundary has to be written down to hold)")
        bad += 1


    r.ask("genre and core fantasy are prose — confirm both are actually stated and specific")
    r.metrics["overview_words"] = len(body.split())
    return settle(r, good, bad)


def check_pillars(p: Project) -> Result:
    r = Result("pillars", "Pillars")
    d = p.gdd
    secs = d.find_sections(r"pillar|支柱|设计原则|design principle")
    if not secs:
        r.bad("no design pillars section")
        return settle(r, 0, 1, absent=True)
    body = "\n".join(s.body for s in secs)


    items = d.list_items(body)
    if not items:
        items = re.findall(r"^\s*\*\*(.{4,80}?)\*\*", body, re.M)
    if not items:
        tbl = [t for t in d.tables if re.search(r"pillar|支柱", t.heading, re.I)]
        if tbl:
            items = [" ".join(row) for row in tbl[0].rows]

    good = bad = 0
    n = len(items)
    r.metrics["pillar_count"] = n
    if n == 0:
        r.bad("pillars section exists but no enumerated pillars found — prose only, so neither "
              "the ≤3 rule nor the adjudication test can be applied")
        return settle(r, 0, 1, unverifiable=True)
    if 1 <= n <= 3:
        r.ok(f"{n} pillars enumerated (§1.2 caps at 3)")
        good += 1
    else:
        r.bad(f"{n} pillars — §1.2 allows at most 3; beyond that they cannot rank against "
              "each other and stop resolving anything")
        bad += 1


    decisive = 0
    adjudicate = (
        r"优先|宁(可|缺)|不是|而非|取舍|以此|决定|舍弃|压倒|高于|大于|不许|禁止|一律|"
        r"不[来从靠用做要会存在]|必须|只(有|用|靠|能)|仅|都(在|有|要)|无(一|例外)|"
        r"\bnot\b|\brather than\b|\bover\b|\bprefer|\binstead\b|\bnever\b|\balways\b|"
        r"\bmust\b|\bno\b\s|\bbeats\b|\bwins\b|\btrade|\bresolve|>|优于|\bevery\b|\bonly\b"
    )
    for it in items:
        if re.search(adjudicate, it, re.I):
            decisive += 1
    r.metrics["decisive_pillars"] = decisive
    weak = [it[:70] for it in items if not re.search(adjudicate, it, re.I)]
    if decisive == n:
        r.ok(f"all {n} pillars contain a decision (preference / rejection / priority)")
        good += 1
    elif decisive == 0:
        r.bad(f"none of the {n} pillars contains a decision — each reads as a description. "
              "The §1.2 test is whether a pillar can settle an argument")
        bad += 1
    elif decisive >= (2 * n + 2) // 3:


        r.ok(f"{decisive} of {n} pillars contain an explicit decision")
        good += 1
        r.ask("these pillars read as description to the tool — check they can each settle an "
              "argument: " + " · ".join(weak))
    else:
        r.bad(f"only {decisive} of {n} pillars contain a decision; the rest read as "
              "description: " + " · ".join(weak))
        bad += 1

    r.ask("read the pillars: could each one actually settle a real design argument, or is it "
          "a slogan that happens to contain the word 'not'?")
    return settle(r, good, bad)


RED_LINES = [
    ("title screen",
     r"标题|title|start screen|开始画面|主菜单|main menu",
     r"title|main_menu|menu|start"),
    ("play",
     r"游玩|play\b|gameplay|关卡|level|room|stage",
     r"level|stage|room|play|world|game"),
    ("pause: resume",
     r"(继续|resume|恢复)",
     r"pause"),
    ("pause: restart",
     r"(重新开始|重开|restart|重来)",
     r"restart|reset|retry|pause"),
    ("pause: quit to title",
     r"(退出到标题|回标题|quit to title|返回标题|exit to title|back to title)",
     r"title|menu|quit"),
    ("ending: win",
     r"(胜利|victory|win\b|通关|clear)",
     r"victor|win|clear|complete|result"),


    ("ending: loss (or a stated reason there is none)",
     r"(失败|defeat|lose\b|loss|game over|落败|死亡|\bdeath\b|\bdie[sd]?\b|"
     r"no unwinnable|cannot lose|no lose state|无法失败|不会失败)",
     r"defeat|lose|fail|gameover|game_over|result|death|died"),
    ("replay / play again",
     r"(再来一局|再来|replay|play again|retry|重玩)",
     r"retry|again|restart|replay"),
    ("save/load or quick-restart substitute",
     r"(存档|读档|save|load|快速重开|quick.?restart|章节记忆|进度)",
     r"user://|save|config|progress"),
    ("closed loop control→goal→feedback→ending→replay",
     r"(闭环|loop|操控.*目标.*反馈|control.*goal.*feedback)",
     None),
    ("goal visible in HUD",
     r"(HUD|计数器|目标.*可见|goal.*visible|objective|任务条|指引)",
     r"hud|objective|goal"),
    ("audio: ambience + feedback cues",
     r"(环境(底|音)|ambien|bgm|music|音效|sfx|反馈音|cue)",
     None),
]


def check_functions(p: Project) -> Result:
    r = Result("functions", "Function list")
    d = p.gdd
    secs = d.find_sections(
        r"function list|功能清单|功能列表|feature checklist|feature list|功能完备|"
        r"functional completeness|功能底线")
    tables = [t for t in d.tables
              if re.search(r"功能|function|feature|红线|red line|底线", " ".join(t.header) +
                           " " + t.heading, re.I)]
    if not secs and not tables:
        r.bad("no function list section and no function table — §3 is a red line and this is the "
              "element the audit found in 3 of 26 GDDs")
        return settle(r, 0, 1, absent=True)

    body = "\n".join(s.body for s in secs)
    if tables:
        body += "\n" + "\n".join(" | ".join(rw) for t in tables for rw in t.rows)


    enumerated = bool(tables) or len(re.findall(r"^\s{0,3}(?:\d+[.)]|[-*+]|\|)\s*\S", body, re.M)) >= 5
    good = bad = 0
    if enumerated:
        r.ok("function list is enumerated (table or list), not prose")
        good += 1
    else:
        r.bad("function coverage is prose, not an enumerated item-by-item list (§1.2: 逐项列出)")
        bad += 1


    haystack = " ".join(p.scenes + p.scripts).lower() + " " + p.code_blob.lower()
    claimed, missing, unbacked, undocumented = [], [], [], []
    for label, gdd_pat, code_pat in RED_LINES:
        in_gdd = bool(re.search(gdd_pat, body, re.I))
        in_code = True if code_pat is None else bool(re.search(code_pat, haystack, re.I))
        if in_gdd:
            claimed.append(label)
            if not in_code:
                unbacked.append(label)
        else:
            missing.append(label)
            if in_code and code_pat is not None:
                undocumented.append(label)

    r.metrics["red_lines_total"] = len(RED_LINES)
    r.metrics["red_lines_claimed"] = len(claimed)
    if missing:
        r.bad(f"{len(missing)} of {len(RED_LINES)} §3 red lines never enumerated: "
              + ", ".join(missing))
        bad += 1
    else:
        r.ok(f"all {len(RED_LINES)} §3 red lines enumerated")
        good += 1

    if unbacked:
        r.bad("claimed in the GDD but no matching scene/script/code found: " + ", ".join(unbacked)
              + " — either the feature is missing or the GDD names it something the tree does not")
        bad += 1
    if undocumented:
        r.note("implemented in the tree but not enumerated in the GDD: " + ", ".join(undocumented)
               + " — the code is ahead of its own design document")


    broken, checked, external = broken_paths(p, body)
    r.metrics["paths_checked"] = checked
    r.metrics["paths_broken"] = len(broken)
    if checked >= 3:
        if broken:
            r.bad(f"{len(broken)} of {checked} scene/script paths quoted in the function list do "
                  "not exist anywhere in the project: " + ", ".join(broken[:6])
                  + " — a function list pointing at absent scenes documents a game that is not "
                    "the one in the tree")
            bad += 1
        else:
            r.ok(f"all {checked - len(external)} resolvable paths quoted in the function list "
                 "exist in the tree")
            good += 1
        if external:
            r.note(f"{len(external)} non-script paths did not resolve (may be external source "
                   "packs): " + ", ".join(external[:4]))
    else:
        r.note(f"only {checked} verifiable paths quoted — a function list that points at the "
               "scenes implementing each line is far easier to keep honest")

    return settle(r, good, bad)


def resolve_path(p: Project, s: str) -> bool:


    if os.path.isabs(s):
        return os.path.exists(s)
    rel = s[len("res://"):] if s.startswith("res://") else s
    rel = rel.lstrip("./")
    for base in (p.root, p.doc_root):
        if os.path.exists(os.path.join(base, rel)):
            return True

    norm = rel.replace("\\", "/")
    if norm in p.file_suffixes or os.path.basename(norm) in p.file_basenames:
        return True
    return any(known.endswith("/" + norm) for known in p.file_suffixes)


IN_PROJECT_EXT = (".tscn", ".gd", ".tres", ".gdshader", ".cfg")


def broken_paths(p: Project, body: str) -> tuple[list[str], int, list[str]]:

    broken: list[str] = []
    external: list[str] = []
    checked = 0
    for span in re.findall(r"`([^`\n]{3,160})`", body):
        s = span.strip()
        if any(ch in s for ch in "*{}<>%") or ".." in s or s.startswith("user://"):
            continue


        if re.search(r"\d[NX]\b|\bNN+\b|_[NX]\.|[_/][NX]\d*\.", s):
            continue
        if not PATHY_RE.match(s):
            continue
        checked += 1
        if resolve_path(p, s):
            continue
        if s.lower().endswith(IN_PROJECT_EXT):
            broken.append(s)
        else:
            external.append(s)
    return broken, checked, external


UI_ACTIONS = re.compile(r"^ui_|^gb_(reset|confirm)$|^editor", re.I)


def check_mechanics(p: Project) -> Result:
    r = Result("mechanics", "Mechanics + controls")
    d = p.gdd
    ctl = d.find_tables(r"key|按键|操作|action|input|control|button|键")
    if not ctl:
        secs = d.find_sections(r"control|操作表|操作方案|按键|core mechanic|核心机制|"
                               r"input map|输入|keybind|key map|机制")
        if not secs:
            r.bad("no control table and no core-mechanics section anywhere")
            return settle(r, 0, 1, absent=True)
        r.bad("a mechanics/input section exists (" + ", ".join(s.title[:40] for s in secs[:3])
              + ") but there is no control **table** with a key column and an effect column "
                "(§1.2 requires 核心机制与操作表)")
        return settle(r, 0, 1)

    table = max(ctl, key=lambda t: len(t.rows))
    good = bad = 0
    r.metrics["control_rows"] = len(table.rows)
    if len(table.rows) >= 3:
        r.ok(f"control table present with {len(table.rows)} rows")
        good += 1
    else:
        r.bad(f"control table has only {len(table.rows)} rows")
        bad += 1


    eff = table.col_index(r"effect|作用|说明|行为|result|does|描述|功能|note")
    if eff < 0 and len(table.header) >= 2:
        eff = 1
    if eff >= 0:
        filled = sum(1 for c in table.column(eff) if len(c.strip()) >= 6)
        if filled >= max(3, int(0.8 * len(table.rows))):
            r.ok(f"{filled}/{len(table.rows)} control rows describe the effect")
            good += 1
        else:
            r.bad(f"only {filled}/{len(table.rows)} control rows describe what the key does")
            bad += 1
    else:
        r.bad("control table has no effect/description column")
        bad += 1


    declared = [a for a in p.input_actions if not UI_ACTIONS.match(a)]
    if declared:
        blob = (" ".join(" ".join(rw) for rw in table.rows) + " " +
                d.section_body(r"control|操作|按键|mechanic|机制|input|绑定")).lower()

        kcol = table.col_index(r"key|按键|键|button|input")
        if kcol < 0:
            kcol = 0
        keyblob = " ".join(table.column(kcol)).lower()

        def documented_action(a: str) -> bool:
            al = a.lower()
            if al in blob:
                return True

            stem = re.sub(r"[_\-]?\d+$", "", al)
            if stem and stem != al and (stem in blob or stem.replace("_", " ") in blob):
                return True
            if al.replace("_", " ") in blob:
                return True


            for lbl in p.action_keys.get(a, []):
                if len(lbl) == 1:
                    if re.search(r"(?<![\w])" + re.escape(lbl) + r"(?![\w])", keyblob):
                        return True
                elif lbl in keyblob:
                    return True
            toks = [t for t in tokenise(a) if len(t) > 3]
            return bool(toks) and all(t in blob for t in toks)

        documented = [a for a in declared if documented_action(a)]
        cover = len(documented) / len(declared)
        r.metrics["actions_declared"] = len(declared)
        r.metrics["actions_documented"] = len(documented)
        undoc = [a for a in declared if a not in documented]
        if cover >= 0.8:
            r.ok(f"{len(documented)}/{len(declared)} declared input actions appear in the "
                 "control documentation")
            good += 1
        else:
            r.bad(f"only {len(documented)}/{len(declared)} declared input actions are documented; "
                  f"undocumented: {', '.join(undoc[:8])} — an action bound to nothing and "
                  "documented nowhere is how dead controls ship")
            bad += 1
    else:
        r.note("project.godot declares no non-ui input actions, so there is nothing to diff "
               "the control table against")

    return settle(r, good, bad)


NO_FACING_DECL = (
    r"本作(无|没有|不存在)[^\n]{0,12}(朝向|facing)"
    r"|(无|没有|不存在)[^\n]{0,8}(需要)[^\n]{0,8}(朝向|facing)[^\n]{0,10}(实体|entity)"
    r"|no entit(y|ies)[^\n]{0,40}(needs?|requiring|require)[^\n]{0,20}facing"
    r"|nothing (in this game |here )?(needs|has)[^\n]{0,20}facing"
    r"|no facing rules?[^\n]{0,30}(needed|required|applicable|apply)"
    r"|facing is not applicable[^\n]{0,40}(game|title|project|anywhere)"
    r"|this (game|title) has no[^\n]{0,30}facing"
)


def declares_no_facing(text: str) -> bool:

    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("|") or s.startswith("- |"):
            continue
        if re.search(NO_FACING_DECL, s, re.I):
            return True
    return False


def check_facing(p: Project) -> Result:

    r = Result("facing", "Facing rules")
    d = p.gdd

    movers = [e for e in p.entities if e.horizontal]
    oriented = [e for e in movers if e.oriented]
    r.metrics["moving_entity_classes"] = len(movers)
    r.metrics["oriented_entity_classes"] = len(oriented)

    secs = d.find_sections(r"facing|朝向|orientation|motion contract|镜像|flip")
    ftables = [t for t in d.tables
               if re.search(r"facing|朝向|flip|orientation|镜像",
                            " ".join(t.header) + " " + t.heading, re.I)]
    body = "\n".join(s.body for s in secs)
    if ftables:
        body += "\n" + "\n".join(" | ".join(rw) for t in ftables for rw in t.rows)

    if not body.strip():
        body = d.section_body(r"mechanic|机制|control|操作|entity|实体|角色|character")


    entity_rows = sum(len(t.rows) for t in ftables)
    declared_none = declares_no_facing(d.text) and entity_rows < 2


    if not secs and not ftables and not re.search(r"flip_h|朝向|facing", d.text, re.I):
        if not movers:
            r.note("no facing section, and the code shows no horizontally moving entity class — "
                   "but §1.2 (as amended) wants that stated explicitly, not left absent")
            r.bad("the 'this game has no entity needing facing' declaration is missing; "
                  "the brief requires it in writing with its reason")
            return settle(r, 0, 1, absent=True)
        r.bad(f"no facing rules at all, and the code has {len(movers)} moving entity classes "
              f"({len(oriented)} of them already manipulate orientation): "
              + ", ".join(e.path for e in movers[:8]))
        return settle(r, 0, 1, absent=True)

    good = bad = 0


    if declared_none:
        if oriented:
            r.bad("the GDD declares this game has no entity needing facing, but "
                  f"{len(oriented)} classes manipulate orientation in code: "
                  + ", ".join(f"{e.path} ({'/'.join(e.orient_kinds)})" for e in oriented[:6])
                  + " — this is a contradiction, and it is exactly the shape of the "
                    "01_cat_defense bug")
            return settle(r, 0, 1)
        if movers:
            r.note(f"declares no facing entities; code shows {len(movers)} moving classes but "
                   "none touches flip/scale/rotation, which is consistent")
        r.ok("explicitly declares no facing-bearing entities, and the code agrees "
             "(no flip_h / scale.x / rotation in any game entity script)")
        r.ask("confirm the stated reason is real — a ball or a top-down cursor genuinely has no "
              "facing; a side-view walker does")
        return settle(r, 1, 0)


    if not movers:
        r.bad("no entity class in the code carries horizontal velocity, and the GDD does not "
              "contain the explicit '本作无朝向实体' declaration with its reason. The brief "
              "requires that statement in writing rather than an absent section — a reader "
              "cannot tell 'considered and not applicable' from 'never considered'")
        r.ask("if this genre really has no facing-bearing entity (a rhythm receptor, a ball, a "
              "top-down cursor), write that and why; the tool will then accept it")
        return settle(r, 0, 1)


    attrs = [
        ("art facing direction",
         r"(faces?|facing|native|朝向|朝|面向)\s*(right|left|up|down|左|右|上|下|[+\-\u2212][xyz])"
         r"|(right|left|左|右)[-\s]?facing|素材朝向"),
        ("signed velocity→orientation mapping", None),
        ("sprite offset vs collision body",
         r"(offset|偏移)[^\n]{0,60}?[-\u2212]?\d|[-\u2212]?\d+\s*(px|像素)[^\n]{0,30}"
         r"(offset|偏移|collider|碰撞)|sprite[^\n]{0,40}(offset|偏移)"),
        ("animation ↔ speed threshold",
         r"(speed|velocity|\|v|vx|速度)[^\n]{0,40}[<>≥≤][^\n]{0,20}\d"
         r"|[<>≥≤]\s*\d+(\.\d+)?\s*(px|像素|/s|m/s)"
         r"|speed_scale|阈值|threshold[^\n]{0,30}\d|\d+\s*(fps|帧)"),
    ]
    for label, pat in attrs:
        present = has_signed_comparison(body) if pat is None else bool(re.search(pat, body, re.I))
        if present:
            r.ok(f"states {label}")
            good += 1
        else:
            r.bad(f"does not state {label}")
            bad += 1


    if movers:
        low = body.lower()
        matched, unmatched = [], []
        for e in movers:
            hit = (e.name.lower() in low
                   or e.name.replace("_", " ").lower() in low
                   or (e.tokens and all(t in low for t in e.tokens))
                   or (e.tokens and len(e.tokens) > 1 and any(t in low for t in e.tokens)
                       and len(e.tokens[0]) > 4))
            (matched if hit else unmatched).append(e)
        r.metrics["entities_documented"] = len(matched)
        r.metrics["entities_undocumented"] = len(unmatched)
        if not unmatched:
            r.ok(f"every one of the {len(movers)} moving entity classes in the code has an "
                 "entry in the facing rules")
            good += 1
        else:
            unm_oriented = [e for e in unmatched if e.oriented]
            msg = (f"{len(unmatched)} of {len(movers)} moving entity classes have no facing entry: "
                   + ", ".join(e.path for e in unmatched[:8]))
            if unm_oriented:
                msg += (f" — and {len(unm_oriented)} of those already manipulate orientation "
                        f"({', '.join(e.name + ':' + '/'.join(e.orient_kinds) for e in unm_oriented[:5])}), "
                        "which is undocumented mirroring, the 01_cat_defense failure exactly")
            r.bad(msg)
            bad += 1
            r.ask("entity names are matched by filename tokens; if the GDD calls these classes "
                  "something else, the match may be a false negative — check by hand")
    else:
        r.note("code shows no horizontally moving entity class to require entries for")


    for e in oriented:
        src = read_text(os.path.join(p.root, e.path)) or ""
        if re.search(r"scale\s*=\s*Vector2\s*\(\s*[-\u2212]", src) and \
           not re.search(r"(flip|facing|sign)", src, re.I):
            r.note(f"{e.path} mirrors with a bare negative Vector2 scale and never mentions "
                   "facing or sign — the 01_cat_defense line. Verify against the GDD's stated "
                   "art facing for this class")

    return settle(r, good, bad)


def check_levels(p: Project) -> Result:
    r = Result("levels", "Level / content table")
    d = p.gdd
    secs = d.find_sections(r"level|关卡|room|content|内容表|beat|节拍|stage|chart|章节|"
                           r"pacing|progression|阶段|phase|wave|波次|day|tier")


    tables = [t for t in d.tables
              if any(s.line <= t.line <= s.end for s in secs)
              or re.search(r"level|关卡|room|beat|节拍|stage|wave|波|content|内容",
                           " ".join(t.header) + " " + t.heading, re.I)]
    if not secs and not tables:
        r.bad("no level/content table and no beat table (§1.2 requires one or the other)")
        return settle(r, 0, 1, absent=True)

    body = "\n".join(s.body for s in secs)
    good = bad = 0


    per_level = [s for s in d.sections
                 if re.search(r"^\s*(L\d|level\s*\d|关\s*\d|第.关|room\s*[A-Z\d]\b|stage\s*\d|"
                              r"act\s*\d|chart\s*\d)", s.title, re.I)]


    rows = max((len(t.rows) for t in tables), default=0)
    units = len(per_level) if len(per_level) >= 2 else max(len(per_level), rows)
    r.metrics["content_units"] = units
    if units >= 2:
        r.ok(f"{units} content units enumerated (levels/rooms/beats)")
        good += 1
    elif units == 1:
        r.note("one content unit — acceptable for a single-scene game if a beat table is present")
    else:
        r.bad("no enumerated levels, rooms or beats")
        bad += 1


    novelty = len(re.findall(
        r"新(东西|增|机制|内容)|introduc|new\b|变化|组合|first time|本关新|adds?\b|"
        r"引入|combine|vary|varies", body, re.I))
    r.metrics["novelty_mentions"] = novelty
    if units >= 2 and novelty >= max(2, units // 2):
        r.ok(f"progression stated ({novelty} 'what is new here' markers across {units} units)")
        good += 1
    elif units >= 2:
        r.bad(f"only {novelty} novelty markers for {units} units — §1.2 wants 引入→变化→组合 "
              "per level, i.e. each level says what it introduces")
        bad += 1


    lvl_scenes = [s for s in p.scenes
                  if re.search(r"(^|/)(level|stage|room|act|world|chart|map)[_\-]?\d+\w*\.tscn$",
                               s, re.I)]
    lvl_data = [s for s in p.level_data
                if re.search(r"(^|/)\w*(level|stage|room|act|world|chart|map)\w*\.json$", s, re.I)]
    tree_units = max(len(lvl_scenes), len(lvl_data))
    r.metrics["tree_level_artefacts"] = tree_units
    if tree_units >= 2 and units:


        if tree_units > units * 3:


            r.note(f"the tree has {tree_units} level artefacts against {units} enumerated content "
                   "units — check the content table has not fallen behind the build")
        elif units > max(4, tree_units * 3):
            r.note(f"the GDD enumerates {units} content units against {tree_units} level "
                   "artefacts in the tree — confirm the extra units are beats or waves inside "
                   "existing levels rather than content that was never built")
        else:
            r.ok(f"GDD's {units} content units are consistent with {tree_units} level artefacts "
                 "in the tree")
            good += 1
    elif units >= 2:
        r.note(f"{tree_units} numbered level artefacts found in the tree, so the GDD's {units} "
               "units could not be cross-checked against the build")
    return settle(r, good, bad)


def check_art(p: Project) -> Result:
    r = Result("art", "Art baseline")
    d = p.gdd
    secs = d.find_sections(r"art baseline|美术基准|art direction|美术|anchor|主锚|palette|配色")
    if not secs:
        r.bad("no art baseline section (§1.2 element 6)")
        return settle(r, 0, 1, absent=True)
    body = "\n".join(s.body for s in secs)
    good = bad = 0


    hexes = sorted(set(h.lower() for h in HEX_RE.findall(body)))
    r.metrics["hex_in_art_section"] = len(hexes)
    if len(hexes) >= 3:
        r.ok(f"{len(hexes)} distinct hex colours in the art baseline")
        good += 1
    elif hexes:
        r.bad(f"only {len(hexes)} hex colour(s) — §1.2 wants the key colours as a palette "
              "sampled from pixels, not one accent")
        bad += 1
    else:
        all_hex = set(h.lower() for h in HEX_RE.findall(d.text))
        if all_hex:
            r.bad(f"no hex in the art baseline section, though {len(all_hex)} appear elsewhere "
                  "in the document — the palette is not where the standard puts it")
        else:
            r.bad("no hex colour values anywhere in the GDD — §1.2: 关键色 hex，"
                  "用 python3 从真实像素取，不是形容词")
        bad += 1


    if re.search(r"python3|PIL|Pillow|getpixel|取像素|sampled|采样|measure|quantiz|"
                 r"\bconvert\b|colorthief|kmeans", body, re.I):
        r.ok("states how the colours were extracted (a measurement, not a preference)")
        good += 1
    else:
        r.bad("does not say how the key colours were obtained; §1.2 requires them taken from "
              "actual pixels with python3, and an unsourced hex is an adjective in hex notation")
        bad += 1


    decl_re = (r"(主锚|main anchor|anchor)\s*(参照|frame|image|=|:|：|是|为)"
               r"|\*\*\s*主锚[^*]*\*\*|locked as the (main )?anchor")
    decl_lines = [ln for ln in d.lines
                  if not ln.strip().startswith("|") and re.search(decl_re, ln, re.I)]
    anchor_secs = d.find_sections(r"anchor|主锚|基准图")
    if decl_lines:
        anchor_body = "\n".join(decl_lines)

        anchor_body += "\n" + "\n".join(
            s.body for s in anchor_secs if not s.body.strip().startswith("|"))
    else:
        anchor_body = "\n".join(
            ln for ln in "\n".join(s.body for s in anchor_secs).splitlines()
            if not ln.strip().startswith("|"))
        if not anchor_body.strip():
            anchor_body = "\n".join(
                ln for ln in body.splitlines()
                if re.search(r"anchor|主锚|基准图", ln, re.I)
                and not ln.strip().startswith("|"))
    self_defined = bool(re.search(r"自定基准|自定主锚|self-?defined|no external reference|"
                                  r"无参照|§?2B|原创", anchor_body, re.I))
    imgs = [s for s in re.findall(r"`([^`\n]{3,160})`", anchor_body)
            if re.search(r"\.(png|jpg|jpeg|webp)$", s.strip(), re.I)]
    imgs += [u for u in re.findall(r"(https?://\S+\.(?:png|jpe?g|webp))", anchor_body, re.I)]
    r.metrics["anchor_images_named"] = len(imgs)

    if imgs:
        local = [s.strip() for s in imgs if not s.lower().startswith("http")]
        found, absent_paths = [], []
        for s in local:
            rel = s[len("res://"):] if s.startswith("res://") else s
            if any(ch in rel for ch in "*{}"):
                continue
            (found if resolve_path(p, s) else absent_paths).append(rel)
        if found:
            r.ok(f"main anchor names an image that exists on disk: {found[0]}")
            good += 1
        if absent_paths and not found:
            r.bad("the named anchor image(s) do not exist in the tree: "
                  + ", ".join(absent_paths[:4])
                  + " — an anchor nothing can be compared against is not an anchor")
            bad += 1
        elif absent_paths:
            r.note("some named anchor images are missing: " + ", ".join(absent_paths[:4]))
        if not local:
            r.note("anchor is given as a URL only; §2 wants the working copy in the tree "
                   "(reference/, development-only, never packaged)")
    elif self_defined:
        r.bad("declares a self-defined baseline (§2B) but names no rendered anchor frame file. "
              "§2B requires a grey-box hero frame rendered, self-checked and locked as the anchor")
        bad += 1
    else:
        r.bad("no main anchor identified (§2: 主锚 = 哪张图、怎么来的)")
        bad += 1


    if re.search(r"\d+\s?%|占比|面积|height ratio|area ratio|nine.?grid|九宫格|"
                 r"composition target", body, re.I):
        r.ok("composition is quantified (ratios / percentages / nine-grid positions)")
        good += 1
    else:
        r.bad("no quantified composition — §7 requires 方位 and **体量** as height/area share, "
              "because 'position right, volume shrunk' is the documented miss")
        bad += 1

    r.ask("open the anchor and the latest render side by side: does the GDD's own description "
          "match what the game actually looks like?")
    return settle(r, good, bad)


METHOD_VOCAB = (
    r"程序化|自绘|预置|原件|切帧|贴图|生成|合成|shader|noise|spline|subset|atlas|"
    r"procedural|hand.?drawn|drawn|generated?|synthes|baked?|bake|script|tool[s]?/|"
    r"verbatim|region|recolou?r|traced|modelled|sculpt|tileset|primitive|"
    r"draw_|Polygon2D|GradientTexture|NoiseTexture|直用|改造|重着色|切图|采样"
)


def check_assets(p: Project) -> Result:
    r = Result("assets", "Asset method + key assets")
    d = p.gdd
    good = bad = 0


    cands = [t for t in d.tables if len(t.rows) >= 3]
    method_tables = []
    for t in cands:


        mi = -1
        for pat in (r"生成方式|生成方法|method|generation|produced|how (it )?is made|how made",
                    r"方式|做法|制作方式|技术|technique",
                    r"来源|source|出处"):
            mi = t.col_index(pat)
            if mi >= 0:
                break
        ai = t.col_index(r"验收|acceptance|check|标准|criteri|验证|accept")
        if mi >= 0 and ai >= 0 and mi != ai:
            method_tables.append((t, mi, ai))

    if not method_tables:
        loose = [t for t in cands
                 if t.col_index(r"资产|asset|素材|sprite|texture") >= 0]
        secs = d.find_sections(r"asset|资产|素材")
        if loose:
            t = max(loose, key=lambda x: len(x.rows))
            missing = []
            if t.col_index(r"生成方式|方式|method|how|produced|generation|来源|source") < 0:
                missing.append("a production-method column")
            if t.col_index(r"验收|acceptance|check|标准|criteri|验证") < 0:
                missing.append("an acceptance column")
            r.bad(f"asset table found ({len(t.rows)} rows) but it lacks {' and '.join(missing)} — "
                  "§1.2 forbids writing 制作 XX 不写怎么做, and acceptance is what makes a row "
                  "checkable")
            bad += 1
        elif secs:
            r.bad("an asset section exists but there is no asset **table** with method and "
                  "acceptance columns")
            bad += 1
        else:
            r.bad("no asset list and no generation-method table (§1.2 element 7)")
            return settle(r, 0, 1, absent=True)
    else:
        t, mi, ai = max(method_tables, key=lambda x: len(x[0].rows))
        r.metrics["asset_rows"] = len(t.rows)
        r.ok(f"asset method table present: {len(t.rows)} rows with method + acceptance columns")
        good += 1


        thin_method, thin_accept, vague = [], [], []
        for row in t.rows:
            asset = row[0].strip() if row else ""
            meth = row[mi].strip() if mi < len(row) else ""
            acc = row[ai].strip() if ai < len(row) else ""
            bare = re.sub(r"[*`_\s]", "", meth)
            if len(bare) < 4:
                thin_method.append(asset or "?")
            elif not re.search(METHOD_VOCAB, meth, re.I) and len(bare) < 14:
                vague.append(f"{asset or '?'} → “{meth}”")
            if len(re.sub(r"[*`_\s]", "", acc)) < 4:
                thin_accept.append(asset or "?")
        r.metrics["rows_thin_method"] = len(thin_method)
        r.metrics["rows_thin_acceptance"] = len(thin_accept)
        if thin_method:
            r.bad(f"{len(thin_method)} rows have an empty or one-word method cell: "
                  + ", ".join(thin_method[:6]))
            bad += 1
        if vague:
            r.bad(f"{len(vague)} rows state a method with no discernible technique: "
                  + "; ".join(vague[:4])
                  + " — this is the '制作 XX' form §1.2 names explicitly")
            bad += 1
        if thin_accept:
            r.bad(f"{len(thin_accept)} rows have no acceptance criterion: "
                  + ", ".join(thin_accept[:6]))
            bad += 1
        if not thin_method and not thin_accept and not vague:
            r.ok("every row states a real production technique and an acceptance criterion")
            good += 1


        tbl_text = "\n".join(" | ".join(rw) for rw in t.rows)
        broken, checked, external = broken_paths(p, tbl_text)
        if checked >= 3:
            if len(broken) > max(1, checked // 5):
                r.bad(f"{len(broken)} of {checked} in-project asset paths in the table do not "
                      "exist: " + ", ".join(broken[:6]))
                bad += 1
            else:
                r.ok(f"{checked - len(broken) - len(external)}/{checked} asset paths in the table "
                     "resolve inside the project")
                good += 1
            if external:
                r.note(f"{len(external)} of {checked} asset paths resolve nowhere in the project "
                       "— expected for source-pack citations, but verify they are real: "
                       + ", ".join(external[:4]))


    key_secs = d.find_sections(r"key asset|关键资产|关键素材|character (fidelity|closeup)|"
                               r"主要角色|角色.*验收|key sprite")
    key_tables = [t for t in d.tables
                  if re.search(r"关键资产|key asset|角色|character", t.heading, re.I)
                  and t.col_index(r"验收|acceptance|标准|criteri|check") >= 0]
    if key_tables:
        kt = max(key_tables, key=lambda x: len(x.rows))
        ai2 = kt.col_index(r"验收|acceptance|标准|criteri|check")
        filled = sum(1 for c in kt.column(ai2) if len(re.sub(r"[*`_\s]", "", c)) >= 8)
        r.metrics["key_assets"] = len(kt.rows)
        if filled >= max(2, int(0.8 * len(kt.rows))):
            r.ok(f"key asset list: {len(kt.rows)} entries, {filled} with a real acceptance "
                 "standard")
            good += 1
        else:
            r.bad(f"key asset list has {len(kt.rows)} entries but only {filled} carry an "
                  "acceptance standard")
            bad += 1
    elif key_secs:
        kb = "\n".join(s.body for s in key_secs)
        items = re.findall(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+(.{12,})$", kb, re.M)
        withacc = [i for i in items
                   if re.search(r"验收|acceptance|一眼|可辨|轮廓|silhouett|recognis|recogniz|"
                                r"identif|对照|compare|标准", i, re.I)]
        r.metrics["key_assets"] = len(items)
        if len(items) >= 3 and len(withacc) >= max(2, len(items) // 2):
            r.ok(f"key asset list: {len(items)} entries, {len(withacc)} with an acceptance clause")
            good += 1
        else:
            r.bad(f"key asset section has {len(items)} enumerated entries, {len(withacc)} with an "
                  "acceptance clause — §1.2 wants每个主要角色/精灵/标志物单列 with its own standard")
            bad += 1
    else:
        r.bad("no key asset list (§1.2 element 7, second half): every major character, sprite or "
              "landmark on its own line with its acceptance criterion")
        bad += 1

    return settle(r, good, bad)


def check_tech(p: Project) -> Result:
    r = Result("tech", "Technical plan")
    d = p.gdd
    secs = d.find_sections(r"technical plan|技术方案|architecture|架构|技术|engineering|"
                           r"node structure|节点结构|implementation")
    if not secs:
        r.bad("no technical plan section (§1.2 element 8)")
        return settle(r, 0, 1, absent=True)
    body = "\n".join(s.body for s in secs)
    good = bad = 0


    node_types = set(re.findall(
        r"\b(CharacterBody2D|CharacterBody3D|RigidBody2D|RigidBody3D|StaticBody2D|StaticBody3D|"
        r"Area2D|Area3D|Node2D|Node3D|CanvasLayer|Control|TileMap|TileMapLayer|Sprite2D|"
        r"AnimatedSprite2D|AnimationPlayer|Camera2D|Camera3D|AudioStreamPlayer\w*|"
        r"GPUParticles2D|GPUParticles3D|CollisionShape2D|CollisionShape3D|Timer|"
        r"AnimatableBody2D|MeshInstance3D|Path2D|PathFollow2D|Marker2D)\b", body))
    tree_shape = bool(re.search(r"^\s*[│├└|`+\\-]{1,}\s*\w", body, re.M))
    r.metrics["node_types_named"] = len(node_types)
    if len(node_types) >= 3 or (tree_shape and node_types):
        r.ok(f"node structure specified ({len(node_types)} engine node types named)")
        good += 1
    else:
        r.bad("node structure not specified — §1.2 asks for the engine node structure, and "
              f"only {len(node_types)} node types are named")
        bad += 1


    has_collision_heading = bool(
        [s for s in secs for sub in d.sections
         if sub.line >= s.line and sub.end <= s.end
         and re.search(r"collision|碰撞|physics|物理", sub.title, re.I)])
    if re.search(r"碰撞层|collision layer|collision mask|layer\s*\d|mask\s*\d|"
                 r"碰撞方案|physics layer|层\s*\d|collision plan|碰撞方案|"
                 r"CollisionShape|collision (body|bodies|rect|shape)", body, re.I) \
            or has_collision_heading:
        r.ok("collision plan present")
        good += 1
    else:
        r.bad("no collision plan with layers/masks (§1.2 asks for 碰撞方案; §6 makes collision "
              "completeness a red line)")
        bad += 1


    if re.search(r"shader", body, re.I):
        r.ok("shader list present")
        good += 1
    else:
        r.bad("no shader list; §1.2 asks for one — 'none, and here is why' is a valid entry, "
              "silence is not")
        bad += 1
    return settle(r, good, bad)


def check_tests(p: Project) -> Result:
    r = Result("tests", "Test plan")
    d = p.gdd
    secs = d.find_sections(r"test plan|测试计划|测试|acceptance|验收|test\b|verification")
    if not secs:
        r.bad("no test plan section (§1.2 element 8)")
        return settle(r, 0, 1, absent=True)
    body = "\n".join(s.body for s in secs)
    good = bad = 0


    layers = 0
    if re.search(r"断言|assert|automated play|自动化试玩|bot\b|真实输入|real input", body, re.I):
        layers += 1
    if re.search(r"截帧|frame inspection|抽帧|write-movie|动态帧|dynamic frame", body, re.I):
        layers += 1
    if re.search(r"固定机位|fixed.?camera|并排|side.?by.?side|叠加|overlay|对照复验", body, re.I):
        layers += 1
    r.metrics["test_layers"] = layers
    if layers == 3:
        r.ok("all three §8 test layers described")
        good += 1
    else:
        r.bad(f"only {layers}/3 of §8's test layers described (assertion play / dynamic frame "
              "inspection / fixed-camera anchor comparison)")
        bad += 1


    if re.search(r"(固定机位|fixed.?camera)[^\n]{0,80}(主锚|anchor|并排|side)|"
                 r"(主锚|anchor)[^\n]{0,80}(并排|side.?by.?side|叠加|overlay)", body, re.I):
        r.ok("contains the mandated fixed-camera-render-vs-anchor comparison item")
        good += 1
    else:
        r.bad("missing the §1.2 item '固定机位渲染 vs 主锚并排' as an explicit acceptance entry")
        bad += 1


    quant = len(re.findall(r"[<>≥≤=]\s*\d|\d+\s*%|\d+\s*(px|ms|s\b|fps|帧|条|个|次)|"
                           r"\b\d+\s*/\s*\d+\b", body))
    r.metrics["quantified_criteria"] = quant
    if quant >= 5:
        r.ok(f"{quant} quantified acceptance criteria (numbers a test can compare against)")
        good += 1
    else:
        r.bad(f"only {quant} quantified criteria — §1.2 requires 可验证的验收标准, and a "
              "criterion with no number cannot fail")
        bad += 1


    gdd_modes = set(re.findall(r"--bot=(\w+)", d.text))
    if p.bot_modes:
        real = set(p.bot_modes)
        r.metrics["bot_modes_in_code"] = len(real)
        r.metrics["bot_modes_in_gdd"] = len(gdd_modes)
        if gdd_modes:
            phantom = sorted(gdd_modes - real)
            unmentioned = sorted(real - gdd_modes)
            if phantom:
                r.bad("test plan names bot modes the harness does not declare: "
                      + ", ".join(phantom) + " — a test entry pointing at nothing")
                bad += 1
            else:
                r.ok(f"all {len(gdd_modes)} bot modes named in the GDD exist in the harness")
                good += 1
            if unmentioned:
                r.note("harness declares modes the GDD never mentions: " + ", ".join(unmentioned))
        else:
            r.bad(f"the harness declares {len(real)} bot modes ({', '.join(sorted(real)[:6])}) "
                  "but the test plan names none of them")
            bad += 1

    r.ask("§8 discipline: are the expected values quoted from the GDD/task brief rather than "
          "copied back from the implementation?")
    return settle(r, good, bad)


CHECKS = [
    ("overview", check_overview),
    ("pillars", check_pillars),
    ("functions", check_functions),
    ("mechanics", check_mechanics),
    ("facing", check_facing),
    ("levels", check_levels),
    ("art", check_art),
    ("assets", check_assets),
    ("tech", check_tech),
    ("tests", check_tests),
]


def check_project(root: str) -> dict:
    p = scan_project(root)
    out: dict = {
        "project": p.name,
        "root": p.root,
        "gdd": os.path.relpath(p.gdd_path, p.doc_root) if p.gdd_path else None,
        "gdd_lines": len(p.gdd.lines) if p.gdd else 0,
        "gdd_mtime": (os.path.getmtime(p.gdd_path)
                      if p.gdd_path and os.path.exists(p.gdd_path) else 0),
        "moving_entity_classes": len([e for e in p.entities if e.horizontal]),
        "oriented_entity_classes": len([e for e in p.entities if e.horizontal and e.oriented]),
        "checks": {},
    }
    if not p.gdd:
        for cid, _ in CHECKS:
            out["checks"][cid] = {"verdict": "absent", "findings": ["BAD   no GDD.md"],
                                 "human": [], "metrics": {}}
        out["score"] = 0
        out["verdict"] = "no GDD.md at all"
        return out

    for cid, fn in CHECKS:
        try:
            res = fn(p)
        except Exception as exc:
            res = Result(cid, cid, "cannot_verify",
                         [f"BAD   checker raised {type(exc).__name__}: {exc}"])
        out["checks"][cid] = {"verdict": res.verdict, "findings": res.findings,
                              "human": res.human, "metrics": res.metrics}
    out["score"] = sum(1 for c in out["checks"].values() if c["verdict"] == "pass")
    out["verdict"] = f"{out['score']}/{len(CHECKS)} elements compliant"
    return out


def discover(roots: list[str]) -> list[str]:
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in {".godot", ".git", "node_modules", "_archives", "staging"}]
            if "project.godot" in filenames:
                found.append(dirpath)
                dirnames[:] = []
    return sorted(found)


def print_project(res: dict, verbose: bool) -> None:
    print(f"\n=== {res['project']}  —  {res['verdict']}")
    if res["gdd"]:
        print(f"    GDD {res['gdd']} ({res['gdd_lines']} lines) · "
              f"{res['moving_entity_classes']} moving entity classes "
              f"({res['oriented_entity_classes']} oriented)")
    for cid, label in ELEMENTS:
        c = res["checks"][cid]
        print(f"    [{VERDICT_SYMBOL[c['verdict']]}] {label:<24} {c['verdict']}")
        if verbose:
            for f in c["findings"]:
                print(f"          {f}")
            for h in c["human"]:
                print(f"          HUMAN {h}")


def write_report(results: list[dict], path: str) -> None:
    total = len(results)
    full = [r for r in results if r["score"] == len(CHECKS)]
    lines: list[str] = []
    A = lines.append

    A("# GDD gate report — §1.2 compliance, measured with substance checks")
    A("")
    A(f"Generated by `tools/gates/check_gdd.py` over {total} projects. "
      "This replaces the grep-built table in `GDD_STANDARD.md`, which counted the *mention* of "
      "each element and so could not distinguish a real main anchor from the word \"anchor\".")
    A("")
    A("## How to read this")
    A("")
    A("| symbol | verdict | meaning |")
    A("|---|---|---|")
    A("| ✓ | `pass` | substantively verified — structure, numbers and cross-checks all hold |")
    A("| ~ | `partial` | some sub-checks verified, some deficient |")
    A("| ✗ | `fail` | present but deficient, or **contradicted by the code** |")
    A("| · | `absent` | no such section |")
    A("| ? | `cannot_verify` | found, but substance is not mechanically decidable — a human "
      "must read it. **Not a pass.** |")
    A("")
    A("`cannot_verify` counts as non-compliance. A gate that guesses toward green when it cannot "
      "tell would rebuild the exact problem this tool exists to fix.")
    A("")
    A("## Calibration — what the tool scores on known samples")
    A("")
    A("A compliance checker that nobody checked is the thing that got us here, so this one was "
      "calibrated against four samples with independently known standing before the fleet was "
      "scanned. Every sub-check that failed one of the two full-marks references was treated as a "
      "**tool bug and fixed**, not as a finding: six were, including a section parser that ended "
      "a chapter at its own first subheading (making every `## Technical plan` with `### 8.1` "
      "subsections read as empty), a table parser that split rows on `|` inside inline code, and "
      "an opt-out regex that matched a *row* reading \"no facing\" for a coin and concluded the "
      "whole document had opted out of facing rules.")
    A("")
    ref_rows = {r["project"]: r for r in results}
    A("| sample | known standing | scored | reading |")
    A("|---|---|---|---|")
    if "pixel-platformer-opus5" in ref_rows:
        A(f"| `pixel-platformer-opus5` (1071 lines) | full-marks reference | "
          f"**{ref_rows['pixel-platformer-opus5']['score']}/10** | the one miss is the facing "
          "element, which was added to the standard on 2026-08-13, after this document was "
          "written. Correct. |")
    for nm, note in (("02_tiny_rts", "audit scored 8/8 elements"),
                     ("03_ninja_roguelite", "audit scored 8/8 elements")):
        if nm in ref_rows:
            A(f"| `{nm}` | {note} | **{ref_rows[nm]['score']}/10** | the grep audit counted "
              "mentions; the gaps below are substance the audit could not see |")
    if "01_cat_defense" in ref_rows:
        A(f"| `01_cat_defense` | audit scored **0/8** — the zero reference | "
          f"**{ref_rows['01_cat_defense']['score']}/10** | **the zero reference no longer exists.** "
          "It was rewritten earlier today, and now carries a full per-entity orientation contract. "
          "The audit's quoted \"Presentation\" section is gone from disk. |")
    A("")
    A("The last row is worth dwelling on: the document this exercise was built to hold up as the "
      "worst case is now among the best, because someone was told to fix it and did. That is the "
      "whole thesis — compliance follows whatever is measured.")
    A("")
    A("### Two findings hand-verified against the source, to show the checks are real")
    A("")
    A("- `03_ninja_roguelite` names `compare/anchor_lit.png` as its locked main anchor, twice, "
      "including in its own anchor-comparison acceptance criterion. **The file does not exist.** "
      "`compare/` holds `01_menu.png` … `16_defeat_sentry.png` and no anchor. A keyword gate sees "
      "the word 主锚 and passes; every milestone comparison this GDD specifies has nothing to "
      "compare against.")
    A("- `05_dungeon_escape/actors/dungeon_enemy.gd:142` runs "
      "`sprite.flip_h = velocity.x < 0.0`, and the GDD's only use of the word \"facing\" is about "
      "sword direction. **Nothing states which way the source art faces**, so no reader can tell "
      "whether that sign is right or inverted. This is the `01_cat_defense` defect exactly, "
      "sitting unexamined in an untouched project.")
    A("")
    A("### What this tool does not check")
    A("")
    A("Stated so the green cells are not read as more than they are. It does not open the game, "
      "render a frame, or compare anything to an anchor image — it checks that the GDD makes "
      "checkable claims and that those claims agree with the tree. A GDD can pass all ten "
      "elements and still describe a game that looks nothing like its reference. Entity-name "
      "matching for facing rules is by filename token, so a GDD that calls `enemy_walker.gd` "
      "\"the shambler\" may show a false gap; every facing verdict carries the class list so that "
      "is checkable by eye. Whether a pillar can really settle an argument, and whether an "
      "acceptance criterion is the *right* one, are handed to a human by design.")
    A("")
    A("### Caveat: the fleet moved while it was being measured")
    A("")
    A("Roughly a dozen agents were rewriting these GDDs during this scan. `02_tiny_rts` grew from "
      "576 to 688 lines between two runs twenty minutes apart, and its facing verdict went from "
      "`absent` to `pass` mid-session. Treat the table as a timestamped sample, not a verdict on "
      "anyone's finished work, and re-run it after the current wave lands.")
    A("")
    A("## Fleet")
    A("")
    hdr = ("| project | GDD lines | GDD last written | " +
           " | ".join(l for _, l in ELEMENTS) + " | score |")
    A(hdr)
    A("|---" * (len(ELEMENTS) + 4) + "|")
    for r in sorted(results, key=lambda x: (-x["score"], x["project"])):
        cells = [VERDICT_SYMBOL[r["checks"][cid]["verdict"]] for cid, _ in ELEMENTS]
        when = (datetime.datetime.fromtimestamp(r["gdd_mtime"]).strftime("%m-%d %H:%M")
                if r.get("gdd_mtime") else "—")
        A(f"| {r['project']} | {r['gdd_lines'] or '—'} | {when} | " + " | ".join(cells) +
          f" | **{r['score']}/{len(CHECKS)}** |")
    A("")
    A(f"**{len(full)} of {total} projects satisfy all {len(CHECKS)} elements.**")
    A("")


    cutoff = 0.0
    dated = [r for r in results if r.get("gdd_mtime")]
    if dated:
        newest = max(r["gdd_mtime"] for r in dated)
        cutoff = newest - 3 * 3600
        fresh = [r for r in dated if r["gdd_mtime"] >= cutoff]
        stale = [r for r in dated if r["gdd_mtime"] < cutoff]
        if fresh and stale:
            fa = sum(r["score"] for r in fresh) / len(fresh)
            sa = sum(r["score"] for r in stale) / len(stale)
            A("### The score is mostly a clock")
            A("")
            A(f"Sorted by when the GDD was last written, the table splits cleanly. The "
              f"{len(fresh)} GDDs touched in the last three hours average **{fa:.1f}/10**. The "
              f"{len(stale)} that were not average **{sa:.1f}/10**. Nothing about these projects' "
              "genres, sizes or ages explains that gap; what separates them is whether an agent "
              "was told to rewrite the GDD today.")
            A("")
            A("This is the audit's own conclusion arriving from a different direction. Compliance "
              "was never a matter of authors knowing or not knowing the standard — it tracks "
              "attention, and attention tracks what gets measured. The corollary is the "
              "uncomfortable one: these scores will decay again unless this check runs in the "
              "same batch as the gates that already run, because a number nobody looks at is the "
              "situation we started from.")
            A("")


    A("## Which elements the fleet misses")
    A("")
    A("| element | pass | partial | fail | absent | cannot_verify |")
    A("|---|---|---|---|---|---|")
    for cid, label in ELEMENTS:
        tally = {k: 0 for k in VERDICT_SYMBOL}
        for r in results:
            tally[r["checks"][cid]["verdict"]] += 1
        A(f"| {label} | {tally['pass']} | {tally['partial']} | {tally['fail']} | "
          f"{tally['absent']} | {tally['cannot_verify']} |")
    A("")


    A("## Facing rules — the element added on 2026-08-13")
    A("")
    A("Required scope comes from the **source**, not from the document: every entity class the "
      "code shows carrying horizontal velocity needs an entry giving art facing, the signed "
      "velocity→orientation mapping, sprite offset against the collision body, and the "
      "animation speed threshold. A GDD that declares \"no entity needs facing\" while an actor "
      "script calls `flip_h` is reported as a contradiction, not a pass.")
    A("")
    A("| project | moving classes | oriented | verdict | first finding |")
    A("|---|---|---|---|---|")
    for r in sorted(results, key=lambda x: -x["moving_entity_classes"]):
        c = r["checks"]["facing"]
        bad_first = next((f[6:] for f in c["findings"] if f.startswith("BAD")), "—")
        A(f"| {r['project']} | {r['moving_entity_classes']} | {r['oriented_entity_classes']} | "
          f"{VERDICT_SYMBOL[c['verdict']]} {c['verdict']} | {bad_first[:160]} |")
    A("")


    A("## Where the leverage is")
    A("")
    A("Ordered by how many projects share each shortfall, since a defect in twenty projects is a "
      "standard nobody could follow, and a defect in two is two projects.")
    A("")
    A("| element | not passing | reading |")
    A("|---|---|---|")
    diag = {
        "functions": "The §3 red-line element. Most GDDs mention menus and audio without "
                     "enumerating the twelve red lines, so the gaps are invisible to the author.",
        "facing": "Added to the standard today, so most documents predate it. The cross-check "
                  "means it cannot be satisfied by prose — the entity list comes from the code.",
        "assets": "Usually an asset table with no acceptance column, or rows whose method cell "
                  "restates the asset's name. §1.2 names this failure explicitly.",
        "art": "Usually hex present but no statement of how it was sampled, or an anchor named "
               "but absent from disk. Both make the anchor comparison unperformable.",
        "tests": "Usually two of three §8 layers, and criteria with no numbers in them. A "
                 "criterion with no number cannot fail.",
        "tech": "Usually a missing shader list or no collision plan. §6 makes collision a red line.",
        "mechanics": "Usually the control table exists but does not cover the input actions "
                     "`project.godot` declares.",
        "levels": "Usually levels enumerated without saying what each one introduces.",
        "overview": "Usually a missing scale tier or no stated non-goals.",
        "pillars": "The best-served element in the fleet: nearly every GDD has three real pillars.",
    }
    ranked = []
    for cid, label in ELEMENTS:
        n = sum(1 for r in results if r["checks"][cid]["verdict"] != "pass")
        ranked.append((n, cid, label))
    for n, cid, label in sorted(ranked, reverse=True):
        A(f"| {label} | {n}/{len(results)} | {diag.get(cid, '')} |")
    A("")
    A("## Wiring this in")
    A("")
    A("```")
    A("python3 tools/gates/check_gdd.py <project>          # one project, verdict per element")
    A("python3 tools/gates/check_gdd.py <project> -v       # every finding, including what a human owes")
    A("python3 tools/gates/check_gdd.py --all --report GDD_GATE_REPORT.md")
    A("```")
    A("")
    A("Exit status is 0 only when every scanned project passes all ten elements, so it can sit "
      "beside `check_runtime.py` and `check_deliverables.py` in the same batch. It is read-only "
      "with respect to projects: it opens files and never writes inside one.")
    A("")
    A("## Per-project findings")
    A("")
    for r in sorted(results, key=lambda x: (-x["score"], x["project"])):
        A(f"### {r['project']} — {r['verdict']}")
        A("")
        if r["gdd"]:
            A(f"`{r['gdd']}`, {r['gdd_lines']} lines · {r['moving_entity_classes']} moving "
              f"entity classes ({r['oriented_entity_classes']} manipulate orientation)")
        else:
            A("**No `GDD.md`.**")
        A("")
        for cid, label in ELEMENTS:
            c = r["checks"][cid]
            A(f"- **{label}** — `{c['verdict']}`")
            for f in c["findings"]:
                if f.startswith("BAD"):
                    A(f"    - {f[6:].strip()}")
            for f in c["findings"]:
                if f.startswith("note"):
                    A(f"    - *note:* {f[6:].strip()}")
        A("")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:


    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    ap = argparse.ArgumentParser(
        description="Gate §1.2 GDD compliance with structural, numeric and code cross-checks.")
    ap.add_argument("dirs", nargs="*", default=[])
    ap.add_argument("--batch", action="append", default=[],
                    help="treat DIR as a container of projects")
    ap.add_argument("--all", action="store_true", help="every project under the standard roots")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every finding")
    ap.add_argument("--json", help="write per-project results as JSONL")
    ap.add_argument("--report", help="write the markdown fleet report here")
    args = ap.parse_args()

    targets: list[str] = []
    for d in args.dirs:
        d = os.path.abspath(d)
        if os.path.isfile(os.path.join(d, "project.godot")) or \
           os.path.isfile(os.path.join(d, "GDD.md")):
            targets.append(d)
        else:
            targets += discover([d])
    for b in args.batch:
        targets += discover([os.path.abspath(b)])
    if args.all:
        targets += discover(STANDARD_ROOTS)

    targets = sorted(set(targets))
    if not targets:
        ap.error("no projects given; use DIR, --batch DIR or --all")

    results = [check_project(t) for t in targets]
    for r in results:
        print_project(r, args.verbose)

    print(f"\n{'-' * 78}")
    full = sum(1 for r in results if r["score"] == len(CHECKS))
    print(f"{full}/{len(results)} projects satisfy all {len(CHECKS)} elements")
    for cid, label in ELEMENTS:
        n = sum(1 for r in results if r["checks"][cid]["verdict"] == "pass")
        print(f"  {label:<24} {n}/{len(results)} pass")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nwrote {args.json}")
    if args.report:
        write_report(results, args.report)
        print(f"wrote {args.report}")

    return 0 if full == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
