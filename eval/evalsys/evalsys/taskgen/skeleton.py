
from __future__ import annotations
import re
from pathlib import Path
STUB_MARKER = '# TASKGEN SKELETON: implement this node. The scene and groups stay.'
_SCRIPT_RESOURCE = re.compile('^\\[ext_resource\\s+type="Script"\\s+path="([^"]+)"\\s+id="([^"]+)"\\]$')
_SCRIPT_ATTACHMENT = re.compile('^script\\s*=\\s*ExtResource\\("([^"]+)"\\)\\s*$')

def scene_script_attachments(path: Path) -> tuple[str, ...]:

    resources: dict[str, str] = {}
    attached: set[str] = set()
    for raw in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = raw.strip()
        declared = _SCRIPT_RESOURCE.match(line)
        if declared:
            resources[declared.group(2)] = declared.group(1)
            continue
        used = _SCRIPT_ATTACHMENT.match(line)
        if used and used.group(1) in resources:
            attached.add(resources[used.group(1)])
    return tuple(sorted(attached))
