

import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_auth.py"
SPEC = importlib.util.spec_from_file_location("check_auth", TOOL)
assert SPEC is not None and SPEC.loader is not None
check_auth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_auth)

KEY = "test-only-not-a-key-4242"
BEARER_HINT = (
    "; hint: this endpoint may expect a Bearer token — set ANTHROPIC_AUTH_TOKEN instead of "
    "ANTHROPIC_API_KEY (e.g. Moonshot/Kimi)."
)
API_KEY_HINT = (
    "; hint: api.anthropic.com expects an API key (x-api-key) — set ANTHROPIC_API_KEY instead "
    "of ANTHROPIC_AUTH_TOKEN."
)


@pytest.fixture
def stub(monkeypatch):


    opener = check_auth.urllib.request.build_opener(check_auth.urllib.request.ProxyHandler({}))
    monkeypatch.setattr(check_auth.urllib.request, "urlopen", opener.open)
    routes: dict[tuple[str, str], int] = {}
    seen: list[tuple[str, str, dict[str, str]]] = []

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}))
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            status = routes.get((self.command, self.path), 404)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"stub"}')

        do_GET = do_POST = _serve

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, routes, seen
    server.shutdown()
    server.server_close()


def test_classifiers():
    assert check_auth.classify_auth(200, "") == "ok"
    assert check_auth.classify_auth(401, "") == "401 unauthorized"
    assert check_auth.classify_auth(403, "") == "403 unauthorized"
    assert check_auth.classify_auth(500, "") == "http 500"
    assert check_auth.classify_auth(None, "Connection refused") == "unreachable (Connection refused)"
    assert check_auth.classify_responses(400, "") == "supports Responses (400 from endpoint)"
    assert check_auth.classify_responses(401, "") == "supports Responses (401 from endpoint)"
    assert check_auth.classify_responses(404, "").startswith("no Responses API (POST /responses -> 404)")
    assert check_auth.classify_responses(405, "").startswith("no Responses API")
    assert check_auth.classify_responses(None, "timeout after 15 s") == "unreachable (timeout after 15 s)"


def test_gateway_that_speaks_responses(stub):
    base, routes, seen = stub
    routes[("GET", "/v1/models")] = 200
    routes[("POST", "/v1/responses")] = 400
    assert check_auth.check_openai_route(f"{base}/v1/", KEY) == "ok; supports Responses (400 from endpoint)"

    assert [(m, p) for m, p, _ in seen] == [("GET", "/v1/models"), ("POST", "/v1/responses")]
    assert all(h.get("authorization") == f"Bearer {KEY}" for _, _, h in seen)


def test_gateway_without_responses_api(stub):
    base, routes, _ = stub
    routes[("GET", "/v1/models")] = 200
    verdict = check_auth.check_openai_route(f"{base}/v1", KEY)
    assert verdict.startswith("ok; no Responses API (POST /responses -> 404)")
    assert "translation proxy" in verdict


def test_rejected_key_on_a_gateway(stub):
    base, routes, _ = stub
    routes[("GET", "/v1/models")] = 401
    routes[("POST", "/v1/responses")] = 401
    assert check_auth.check_openai_route(f"{base}/v1", KEY) == (
        "401 unauthorized; supports Responses (401 from endpoint)"
    )


def test_official_openai_gets_no_responses_probe(monkeypatch):
    calls = []

    def fake_probe(method, url, headers, body=None, timeout=15):
        calls.append((method, url))
        return 401, ""

    monkeypatch.setattr(check_auth, "probe", fake_probe)
    assert check_auth.check_openai_route(check_auth.OFFICIAL_OPENAI, KEY) == "401 unauthorized"
    assert calls == [("GET", "https://api.openai.com/v1/models")]


def test_unreachable_endpoint():
    verdict = check_auth.check_openai_route("http://127.0.0.1:1/v1", KEY)
    assert verdict.startswith("unreachable (")
    assert ";" not in verdict


def test_anthropic_official_style(stub):
    base, routes, seen = stub
    routes[("GET", "/v1/models")] = 200
    assert check_auth.check_anthropic_route(base, KEY) == "ok"
    assert seen[0][2].get("x-api-key") == KEY and seen[0][2].get("anthropic-version")
    routes[("GET", "/v1/models")] = 401

    assert check_auth.check_anthropic_route(base, KEY) == "401 unauthorized" + BEARER_HINT


def test_anthropic_compatible_gateway_without_models_list(stub):
    base, routes, seen = stub
    routes[("POST", "/anthropic/v1/messages")] = 400
    verdict = check_auth.check_anthropic_route(f"{base}/anthropic/", KEY)
    assert verdict.startswith("ok (no /v1/models: 404; POST /v1/messages accepted the key)")
    assert [(m, p) for m, p, _ in seen] == [("GET", "/anthropic/v1/models"), ("POST", "/anthropic/v1/messages")]
    seen.clear()
    routes[("POST", "/anthropic/v1/messages")] = 401
    assert check_auth.check_anthropic_route(f"{base}/anthropic", KEY) == "401 unauthorized" + BEARER_HINT
    del routes[("POST", "/anthropic/v1/messages")]
    assert check_auth.check_anthropic_route(f"{base}/anthropic", KEY) == (
        "no Messages API (/v1/models -> 404, /v1/messages -> 404)"
    )


def test_anthropic_bearer_token_route_sends_authorization_not_x_api_key(stub):
    base, routes, seen = stub
    routes[("GET", "/anthropic/v1/models")] = 200
    assert check_auth.check_anthropic_route(f"{base}/anthropic", KEY, bearer=True) == "ok"
    headers = seen[0][2]
    assert headers.get("authorization") == f"Bearer {KEY}"
    assert "x-api-key" not in headers and headers.get("anthropic-version")

    seen.clear()
    del routes[("GET", "/anthropic/v1/models")]
    routes[("POST", "/anthropic/v1/messages")] = 400
    verdict = check_auth.check_anthropic_route(f"{base}/anthropic", KEY, bearer=True)
    assert verdict.startswith("ok (no /v1/models: 404; POST /v1/messages accepted the key)")
    assert all(h.get("authorization") == f"Bearer {KEY}" and "x-api-key" not in h for _, _, h in seen)


def test_401_credential_hints_depend_on_header_and_endpoint(stub, monkeypatch):
    base, routes, _ = stub
    routes[("GET", "/a/v1/models")] = 401

    assert check_auth.check_anthropic_route(f"{base}/a", KEY) == "401 unauthorized" + BEARER_HINT
    assert check_auth.check_anthropic_route(f"{base}/a", KEY, bearer=True) == "401 unauthorized"

    routes[("GET", "/a/v1/models")] = 403
    assert check_auth.check_anthropic_route(f"{base}/a", KEY) == "403 unauthorized"
    del routes[("GET", "/a/v1/models")]
    routes[("POST", "/a/v1/messages")] = 401
    assert check_auth.check_anthropic_route(f"{base}/a", KEY, bearer=True) == "401 unauthorized"
    assert check_auth.check_anthropic_route(f"{base}/a", KEY).endswith(BEARER_HINT)


    monkeypatch.setattr(check_auth, "probe", lambda method, url, headers, body=None, timeout=15: (401, ""))
    assert check_auth.check_anthropic_route(check_auth.OFFICIAL_ANTHROPIC, KEY, bearer=True) == (
        "401 unauthorized" + API_KEY_HINT
    )
    assert check_auth.check_anthropic_route(check_auth.OFFICIAL_ANTHROPIC, KEY) == "401 unauthorized"

    env = {"GB_AUTH_CODEX_KIND": "custom", "GB_AUTH_CODEX_KEY_ENV": "OPENAI_API_KEY",
           "GB_AUTH_CODEX_BASE_URL": f"{base}/v1",
           "GB_AUTH_CLAUDE_KEY_ENV": "ANTHROPIC_AUTH_TOKEN",
           "GB_AUTH_CLAUDE_BASE_URL": check_auth.OFFICIAL_ANTHROPIC, "ANTHROPIC_AUTH_TOKEN": KEY}
    table, all_ok = check_auth.rows(env)
    assert not all_ok and table[1][3] == "401 unauthorized" + API_KEY_HINT
    assert KEY not in table[1][3]


def test_rows_use_bearer_only_when_the_route_key_is_anthropic_auth_token(stub):
    base, routes, seen = stub
    routes[("GET", "/a/v1/models")] = 200
    env = {"GB_AUTH_CODEX_KIND": "custom", "GB_AUTH_CODEX_KEY_ENV": "OPENAI_API_KEY",
           "GB_AUTH_CODEX_BASE_URL": f"{base}/v1",
           "GB_AUTH_CLAUDE_KEY_ENV": "ANTHROPIC_AUTH_TOKEN", "GB_AUTH_CLAUDE_BASE_URL": f"{base}/a",
           "ANTHROPIC_AUTH_TOKEN": KEY + "t", "ANTHROPIC_API_KEY": ""}
    table, all_ok = check_auth.rows(env)
    assert all_ok
    assert table[1][:3] == ("claude", "ANTHROPIC_AUTH_TOKEN", f"{base}/a") and table[1][3] == "ok"
    assert [(m, p) for m, p, _ in seen] == [("GET", "/a/v1/models")]
    assert seen[0][2].get("authorization") == f"Bearer {KEY}t" and "x-api-key" not in seen[0][2]

    seen.clear()
    env.update({"GB_AUTH_CLAUDE_KEY_ENV": "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY": KEY})
    table, _ = check_auth.rows(env)
    assert table[1][1] == "ANTHROPIC_API_KEY" and table[1][3] == "ok"
    assert seen[0][2].get("x-api-key") == KEY and "authorization" not in seen[0][2]


def test_rows_with_nothing_filled_reports_not_set_and_passes(monkeypatch):
    monkeypatch.setattr(check_auth, "codex_login_status", lambda env: "`codex login status` (exit 1): Not logged in")
    env = {"GB_AUTH_CODEX_KIND": "openai", "GB_AUTH_CODEX_KEY_ENV": "OPENAI_API_KEY",
           "GB_AUTH_CLAUDE_KEY_ENV": "ANTHROPIC_API_KEY",
           "GB_AUTH_CLAUDE_BASE_URL": "https://api.anthropic.com",
           "CODEX_AUTH_FILE": "/nonexistent/auth.json"}
    table, all_ok = check_auth.rows(env)
    assert all_ok
    assert [(r[0], r[1]) for r in table] == [("codex", "OPENAI_API_KEY"), ("claude", "ANTHROPIC_API_KEY")]
    assert table[0][3] == "not set; `codex login status` (exit 1): Not logged in; /nonexistent/auth.json absent"
    assert table[1][3] == "not set"


def test_rows_probe_each_filled_route_and_never_leak_the_value(stub, capsys, monkeypatch):
    base, routes, _ = stub
    routes[("GET", "/v1/models")] = 200
    routes[("POST", "/v1/responses")] = 400
    routes[("GET", "/a/v1/models")] = 401
    env = {
        "GB_AUTH_CODEX_KIND": "custom", "GB_AUTH_CODEX_KEY_ENV": "OPENAI_API_KEY",
        "GB_AUTH_CODEX_BASE_URL": f"{base}/v1", "OPENAI_API_KEY": KEY, "OPENAI_BASE_URL": f"{base}/v1",
        "GB_AUTH_CLAUDE_KEY_ENV": "ANTHROPIC_API_KEY", "GB_AUTH_CLAUDE_BASE_URL": f"{base}/a",
        "ANTHROPIC_API_KEY": KEY + "b",
        "GAMEBENCH_VLM_PROVIDER": "responses", "GAMEBENCH_VLM_KEY_ENV": "OPENAI_API_KEY",
    }
    table, all_ok = check_auth.rows(env)
    assert not all_ok
    assert [r[0] for r in table] == ["codex", "claude", "vlm"]
    assert table[0][3] == "ok; supports Responses (400 from endpoint)"
    assert table[1][3] == "401 unauthorized" + BEARER_HINT

    assert table[2][1:] == ("OPENAI_API_KEY", f"{base}/v1", "ok; supports Responses (400 from endpoint)")
    monkeypatch.setattr(check_auth.os, "environ", env)
    rc = check_auth.main()
    out = capsys.readouterr().out
    assert rc == 1
    assert KEY not in out
    assert "Authorization:" not in out and "x-api-key:" not in out
    assert BEARER_HINT in out
    assert "codex   OPENAI_API_KEY" in out and "claude  ANTHROPIC_API_KEY" in out
