#!/usr/bin/env python3


from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request

TIMEOUT_S = 15
OFFICIAL_OPENAI = "https://api.openai.com/v1"
OFFICIAL_ANTHROPIC = "https://api.anthropic.com"


BEARER_KEY_ENV = "ANTHROPIC_AUTH_TOKEN"

JUDGE_RESPONSES_KEYS = ("MICU_API_KEY", "OPENAI_API_KEY", "AUTO_CODE_API_KEY")
JUDGE_RESPONSES_BASES = ("GAMEBENCH_VLM_BASE_URL", "OPENAI_BASE_URL", "AUTO_CODE_BASE_URL")
JUDGE_RESPONSES_DEFAULT_BASE = "https://www.micuapi.ai/v1"
JUDGE_ANTHROPIC_KEYS = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "AUTO_CODE_API_KEY")
JUDGE_ANTHROPIC_BASES = ("GAMEBENCH_VLM_BASE_URL", "ANTHROPIC_BASE_URL", "CLAUDE_BASE_URL")


def probe(method: str, url: str, headers: dict[str, str], body: bytes | None = None,
          timeout: float = TIMEOUT_S) -> tuple[int | None, str]:

    status, _detail = probe_detail(method, url, headers, body, timeout)
    return status, _detail if status is None else ""


def probe_detail(method: str, url: str, headers: dict[str, str], body: bytes | None = None,
                 timeout: float = TIMEOUT_S) -> tuple[int | None, str]:


    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, ""
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, (exc.read() or b"").decode("utf-8", "replace")[:400]
        except OSError:
            return exc.code, ""
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return None, f"timeout after {timeout:g} s"
        return None, str(reason)
    except (socket.timeout, TimeoutError):
        return None, f"timeout after {timeout:g} s"
    except (OSError, ValueError) as exc:
        return None, exc.__class__.__name__


def classify_auth(status: int | None, reason: str) -> str:
    if status is None:
        return f"unreachable ({reason})"
    if status == 200:
        return "ok"
    if status in (401, 403):
        return f"{status} unauthorized"
    return f"http {status}"


def classify_responses(status: int | None, reason: str) -> str:
    if status is None:
        return f"unreachable ({reason})"
    if status in (404, 405):


        if "model" in reason.lower():
            return (f"supports Responses ({status} from endpoint: no model in the "
                    "probe body, which is expected)")
        return (f"no Responses API (POST /responses -> {status}); codex won't work against "
                "this URL, put a Responses translation proxy in front of it")
    if 400 <= status < 500:
        return f"supports Responses ({status} from endpoint)"
    if status == 200:
        return "supports Responses"
    return f"Responses probe: http {status}"


def check_openai_route(base_url: str, key: str) -> str:
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {key}"}
    verdict = classify_auth(*probe("GET", f"{base}/models", headers))
    if base == OFFICIAL_OPENAI or verdict.startswith("unreachable"):
        return verdict
    responses = probe_detail("POST", f"{base}/responses",
                             {**headers, "Content-Type": "application/json"}, b"{}")
    return f"{verdict}; {classify_responses(*responses)}"


def _with_credential_hint(verdict: str, status: int | None, base: str, bearer: bool) -> str:


    if status != 401:
        return verdict
    if not bearer and base != OFFICIAL_ANTHROPIC:
        return (f"{verdict}; hint: this endpoint may expect a Bearer token — set "
                f"{BEARER_KEY_ENV} instead of ANTHROPIC_API_KEY (e.g. Moonshot/Kimi).")
    if bearer and base == OFFICIAL_ANTHROPIC:
        return (f"{verdict}; hint: api.anthropic.com expects an API key (x-api-key) — set "
                f"ANTHROPIC_API_KEY instead of {BEARER_KEY_ENV}.")
    return verdict


def check_anthropic_route(base_url: str, key: str, *, bearer: bool = False) -> str:


    base = base_url.rstrip("/")
    credential = {"Authorization": f"Bearer {key}"} if bearer else {"x-api-key": key}
    headers = {**credential, "anthropic-version": "2023-06-01"}
    status, reason = probe("GET", f"{base}/v1/models", headers)
    if status not in (404, 405):
        return _with_credential_hint(classify_auth(status, reason), status, base, bearer)

    status2, reason2 = probe("POST", f"{base}/v1/messages",
                             {**headers, "Content-Type": "application/json"}, b"{}")
    if status2 == 400:
        return f"ok (no /v1/models: {status}; POST /v1/messages accepted the key)"
    if status2 in (401, 403):
        return _with_credential_hint(f"{status2} unauthorized", status2, base, bearer)
    if status2 in (404, 405):
        return f"no Messages API (/v1/models -> {status}, /v1/messages -> {status2})"
    if status2 is None:
        return f"unreachable ({reason2})"
    return f"http {status2} from /v1/messages (/v1/models -> {status})"


def codex_login_status(env: dict[str, str]) -> str:
    codex = shutil.which("codex", path=env.get("PATH"))
    if not codex:
        return "codex CLI not on PATH (run ./setup.sh)"
    try:
        proc = subprocess.run([codex, "login", "status"], capture_output=True, text=True,
                              timeout=TIMEOUT_S, check=False, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"`codex login status` timed out after {TIMEOUT_S} s"
    except OSError as exc:
        return f"`codex login status` failed to start ({exc.__class__.__name__})"
    lines = [line for line in (proc.stdout + proc.stderr).splitlines()
             if line.strip() and "UNDICI" not in line and "trace-warnings" not in line]
    text = lines[0].strip() if lines else f"no output (exit {proc.returncode})"
    return f"`codex login status` (exit {proc.returncode}): {text}"


def _first(env: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        if env.get(name):
            return name
    return ""


def rows(env: dict[str, str]) -> tuple[list[tuple[str, str, str, str]], bool]:

    out: list[tuple[str, str, str, str]] = []
    failed = False

    def add(name: str, key_env: str, url: str, verdict: str, *, counts: bool = True) -> None:
        nonlocal failed
        out.append((name, key_env, url, verdict))
        if counts and not verdict.startswith("ok"):
            failed = True


    kind = env.get("GB_AUTH_CODEX_KIND", "openai")
    key_env = env.get("GB_AUTH_CODEX_KEY_ENV") or "OPENAI_API_KEY"
    base = env.get("GB_AUTH_CODEX_BASE_URL") or OFFICIAL_OPENAI
    key = env.get(key_env, "")
    if kind == "openai" and not key:
        auth = os.path.expanduser(env.get("CODEX_AUTH_FILE") or "~/.codex/auth.json")
        state = "present" if os.path.isfile(auth) else "absent"
        add("codex", key_env, "(native ChatGPT login)",
            f"not set; {codex_login_status(env)}; {auth} {state}", counts=False)
    elif not key:
        add("codex", key_env, base, "not set", counts=False)
    else:
        add("codex", key_env, base, check_openai_route(base, key))


    key_env = env.get("GB_AUTH_CLAUDE_KEY_ENV") or "ANTHROPIC_API_KEY"
    base = env.get("GB_AUTH_CLAUDE_BASE_URL") or OFFICIAL_ANTHROPIC
    key = env.get(key_env, "")
    if not key:
        add("claude", key_env, base, "not set", counts=False)
    else:
        add("claude", key_env, base,
            check_anthropic_route(base, key, bearer=key_env == BEARER_KEY_ENV))


    if any(env.get(name) for name in ("GAMEBENCH_VLM_PROVIDER", "GAMEBENCH_VLM_MODEL",
                                       "GAMEBENCH_VLM_BASE_URL", "GAMEBENCH_VLM_KEY_ENV")):
        provider = (env.get("GAMEBENCH_VLM_PROVIDER") or "responses").strip().lower()
        key_env = env.get("GAMEBENCH_VLM_KEY_ENV") or ""
        if provider == "anthropic":
            key_env = key_env or _first(env, JUDGE_ANTHROPIC_KEYS) or "ANTHROPIC_API_KEY"
            base = env.get(_first(env, JUDGE_ANTHROPIC_BASES), "") or OFFICIAL_ANTHROPIC
            checker = check_anthropic_route
        else:
            key_env = key_env or _first(env, JUDGE_RESPONSES_KEYS) or "OPENAI_API_KEY"
            base = env.get(_first(env, JUDGE_RESPONSES_BASES), "") or JUDGE_RESPONSES_DEFAULT_BASE
            checker = check_openai_route
        key = env.get(key_env, "")
        if provider not in ("responses", "anthropic"):
            add("vlm", key_env, base, f"GAMEBENCH_VLM_PROVIDER={provider!r} is not responses|anthropic")
        elif not key:
            add("vlm", key_env, base, "not set", counts=False)
        else:
            add("vlm", key_env, base, checker(base, key))
    return out, not failed


def main() -> int:
    env = dict(os.environ)
    table, all_ok = rows(env)
    print(f"{'harness':<8}{'key variable':<22}{'endpoint':<40}verdict")
    for name, key_env, url, verdict in table:
        print(f"{name:<8}{key_env:<22}{url:<40}{verdict}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
