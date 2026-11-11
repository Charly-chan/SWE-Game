
from __future__ import annotations
import hashlib
import json
from typing import Any, Iterable, Mapping
CALIBRATION_SCHEMA = 'gamebench.unity-calibration.v1'

def suite_content_digest(payload: Mapping[str, Any]) -> str:

    content = {key: value for key, value in payload.items() if key not in {'content_digest', 'status', 'runtime_ready', 'blockers'}}
    encoded = json.dumps(content, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return 'sha256:' + hashlib.sha256(encoded).hexdigest()
