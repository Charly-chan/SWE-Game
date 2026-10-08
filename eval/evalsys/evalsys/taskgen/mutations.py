
from __future__ import annotations
import re
from pathlib import Path
SKIP_DIRS = frozenset({'.godot', '.git', '__pycache__', 'inputs', 'recording', 'verify', 'compare', 'bot', 'hidden', 'interface'})
GOAL_GROUP_TSCN_RE = re.compile('(groups\\s*=\\s*\\[)([^\\]]*)(\\])')
ADD_TO_GROUP_NAME_RE = re.compile('add_to_group\\(\\s*(?:&)?["\']([^"\']+)["\']')
GROUP_LITERAL_RE = re.compile('(?:&)?["\'](gb_[a-z_]+)["\']')

def _iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob('*'):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any((part in SKIP_DIRS for part in path.parts)):
            continue
        out.append(path)
    return sorted(out, key=lambda path: _rel(root, path))

def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace('\\', '/')

def _without_gdscript_comments(text: str) -> str:

    out: list[str] = []
    for line in text.splitlines(keepends=True):
        quote = ''
        escaped = False
        for char in line:
            if quote:
                out.append(char)
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == quote:
                    quote = ''
            elif char in {'"', "'"}:
                quote = char
                out.append(char)
            elif char == '#':
                if line.endswith('\n'):
                    out.append('\n')
                break
            else:
                out.append(char)
    return ''.join(out)

def _balanced_end(text: str, opening: int, opener: str, closer: str) -> int | None:
    depth = 0
    quote = ''
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = ''
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
    return None

def _split_arguments(text: str) -> list[str]:

    out: list[str] = []
    start = 0
    stack: list[str] = []
    pairs = {')': '(', ']': '[', '}': '{'}
    quote = ''
    escaped = False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = ''
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in '([{':
            stack.append(char)
        elif char in ')]}' and stack and (stack[-1] == pairs[char]):
            stack.pop()
        elif char == ',' and (not stack):
            out.append(text[start:index].strip())
            start = index + 1
    out.append(text[start:].strip())
    return out

def _function_body(text: str, head: re.Match[str], close: int) -> str:
    line_end = text.find('\n', close)
    if line_end < 0 or ':' not in text[close + 1:line_end]:
        return ''
    indent = len(head.group('indent').replace('\t', '    '))
    body_end = len(text)
    cursor = line_end + 1
    while cursor < len(text):
        next_end = text.find('\n', cursor)
        next_end = len(text) if next_end < 0 else next_end
        line = text[cursor:next_end]
        if line.strip():
            leading = len(line) - len(line.lstrip(' \t'))
            width = len(line[:leading].replace('\t', '    '))
            if width <= indent:
                body_end = cursor
                break
        cursor = next_end + 1
    return text[line_end + 1:body_end]

def _forwarded_literal_groups(text: str) -> set[str]:


    found: set[str] = set()
    head_re = re.compile('(?m)^(?P<indent>[ \\t]*)func[ \\t]+(?P<name>[A-Za-z_]\\w*)[ \\t]*(?P<open>\\()')
    for head in head_re.finditer(text):
        opening = head.start('open')
        close = _balanced_end(text, opening, '(', ')')
        if close is None:
            continue
        params = _split_arguments(text[opening + 1:close])
        body = _function_body(text, head, close)
        if not body:
            continue
        forwarded: list[int] = []
        for index, raw_param in enumerate(params):
            name = raw_param.split(':', 1)[0].split('=', 1)[0].strip()
            if not re.fullmatch('[A-Za-z_]\\w*', name):
                continue
            loops = re.findall(f'\\bfor\\s+([A-Za-z_]\\w*)\\s+in\\s+{re.escape(name)}\\s*:', body)
            if any((re.search(f'\\badd_to_group\\s*\\(\\s*{re.escape(loop_var)}\\s*\\)', body) for loop_var in loops)):
                forwarded.append(index)
        if not forwarded:
            continue
        call_re = re.compile(f"\\b{re.escape(head.group('name'))}\\s*(\\()")
        for call in call_re.finditer(text):
            line_start = text.rfind('\n', 0, call.start()) + 1
            if re.match('\\s*func\\b', text[line_start:call.start()]):
                continue
            call_close = _balanced_end(text, call.start(1), '(', ')')
            if call_close is None:
                continue
            args = _split_arguments(text[call.start(1) + 1:call_close])
            for index in forwarded:
                if index >= len(args):
                    continue
                argument = args[index].strip()
                if argument.startswith('[') and argument.endswith(']'):
                    found.update(GROUP_LITERAL_RE.findall(argument))
    return found

def declared_groups(root: Path) -> set[str]:


    found: set[str] = set()
    for path in _iter_files(root, ('.tscn',)):
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.lstrip().startswith(';'):
                continue
            match = GOAL_GROUP_TSCN_RE.search(line)
            if not match:
                continue
            for part in match.group(2).split(','):
                name = part.strip().strip('"').strip("'")
                if name:
                    found.add(name)
    for path in _iter_files(root, ('.gd',)):
        text = _without_gdscript_comments(path.read_text(encoding='utf-8', errors='replace'))
        for line in text.splitlines():
            for match in ADD_TO_GROUP_NAME_RE.finditer(line):
                name = match.group(1).strip()
                if name:
                    found.add(name)
        found.update(_forwarded_literal_groups(text))
    return found
