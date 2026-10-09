"""Read the minimal CC provider configuration without copying a user's home."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Iterator


@contextmanager
def provider_settings(args) -> Iterator[None]:
    source = getattr(args, "claude_settings", "")
    if not source:
        yield
        return
    if args.harness != "claude":
        raise ValueError("--claude-settings requires --harness claude")
    try:
        settings = json.loads(Path(source).expanduser().read_text(encoding="utf-8-sig"))
        configured = settings.get("env") or {}
        if not isinstance(configured, dict):
            raise ValueError()
    except (OSError, ValueError, AttributeError):
        raise ValueError("CC settings file is unavailable or malformed") from None
    key = "ANTHROPIC_AUTH_TOKEN" if configured.get("ANTHROPIC_AUTH_TOKEN") else "ANTHROPIC_API_KEY"
    if not configured.get(key):
        raise ValueError("CC settings contain no API credential; OAuth needs explicit auth configuration")
    model = args.model or settings.get("model") or configured.get("ANTHROPIC_MODEL") or configured.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
    if not model:
        raise ValueError("specify --model or a CC model in settings")
    args.model = str(model)
    args.agent_base_url = str(configured.get("ANTHROPIC_BASE_URL") or "")
    args.agent_key_env = key
    args.agent_provider = "custom"
    other_key = "ANTHROPIC_API_KEY" if key == "ANTHROPIC_AUTH_TOKEN" else "ANTHROPIC_AUTH_TOKEN"
    allowed = (key, other_key, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "API_TIMEOUT_MS")
    previous = {name: os.environ.get(name) for name in allowed}
    try:
        os.environ.pop(other_key, None)
        for name in allowed:
            if name != other_key and name in configured:
                os.environ[name] = str(configured[name])
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
