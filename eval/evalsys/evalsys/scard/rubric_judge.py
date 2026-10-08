
from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time


@contextmanager
def _serial_lock(path: Path):


    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield lock
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield lock
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


class ClaudeCodeRubricJudge:
    def __init__(self):
        self.model = os.environ.get("GAMEBENCH_VLM_MODEL", "claude-fable-5-1")
        self.effort = os.environ.get("GAMEBENCH_VLM_EFFORT", "medium")
        self.base_url = os.environ.get("GAMEBENCH_VLM_BASE_URL", "https://www.micuapi.ai")
        self.key_env = os.environ.get("GAMEBENCH_VLM_KEY_ENV", "MICU_API_KEY")
        self.executable = os.environ.get("GAMEBENCH_CLAUDE_CODE_BIN") or shutil.which("claude")

    def validate_configuration(self):
        key = os.environ.get(self.key_env, "")
        if not key:
            raise RuntimeError(f"VLM evaluation needs a key in {self.key_env}")
        if not self.executable or not shutil.which(self.executable):
            raise RuntimeError("Claude Code is required; set GAMEBENCH_CLAUDE_CODE_BIN or add claude to PATH")
        return key

    def score(self, prompt, images, out):
        from ..taskgen.package import write_json

        key = self.validate_configuration()
        out = Path(out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        (out / "prompt.md").write_text(prompt, encoding="utf-8")
        content = [{"type": "text", "text": prompt}]
        for image in images:
            path = Path(image["path"])
            content.extend([
                {"type": "text", "text": image["label"]},
                {"type": "image", "source": {"type": "base64",
                 "media_type": mimetypes.guess_type(path)[0] or "image/png",
                 "data": base64.b64encode(path.read_bytes()).decode("ascii")}},
            ])
        payload = json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False) + "\n"
        env = dict(os.environ)
        for name in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_MODEL"):
            env.pop(name, None)
        if "micuapi.ai" in self.base_url:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env.pop(name, None)
        env.update(ANTHROPIC_API_KEY=key, ANTHROPIC_BASE_URL=self.base_url.rstrip("/").removesuffix("/v1"),
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1")
        command = [self.executable, "--print", "--bare", "--model", self.model,
                   "--effort", self.effort, "--permission-mode", "dontAsk",
                   "--tools", "Read", "--allowedTools", "Read", "--no-session-persistence",
                   "--disable-slash-commands", "--setting-sources", "", "--verbose",
                   "--input-format", "stream-json", "--output-format", "stream-json"]
        started = time.monotonic()
        models, image_reads = set(), []
        final = None
        with _serial_lock(Path(tempfile.gettempdir()) / "gamebench-vlm-serial.lock"), \
                tempfile.TemporaryDirectory(prefix="gamebench-rubric-claude-") as config:

            env["CLAUDE_CONFIG_DIR"] = config
            proc = subprocess.Popen(command, cwd=out, env=env, text=True,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            proc.stdin.write(payload)
            proc.stdin.close()
            with (out / "trace.jsonl").open("w", encoding="utf-8") as trace:
                for line in proc.stdout:
                    line = line.replace(key, "[REDACTED]")
                    trace.write(line)
                    trace.flush()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message = event.get("message") or {}
                    if message.get("model"):
                        models.add(message["model"])
                    for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                        if block.get("type") == "tool_use" and block.get("name") == "Read":
                            image_reads.append(block.get("input", {}).get("file_path"))
                    if event.get("type") == "result":
                        final = event
            code = proc.wait()
        record = {"transport": "claude_code", "provider": self.base_url,
                  "requested_model": self.model, "returned_models": sorted(models),
                  "elapsed_s": round(time.monotonic() - started, 2), "exit_code": code,
                  "attached_images": images, "requested_reads": image_reads,
                  "raw_response": str(out / "result.json")}
        write_json(out / "result.json", final)
        write_json(out / "request.json", record)
        if code or not final or final.get("is_error"):
            reason = str((final or {}).get("result") or f"Claude Code exited {code}")
            reason = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", reason.replace(key, "[REDACTED]"))
            raise RuntimeError(reason[:1600])
        if models != {self.model}:
            raise RuntimeError(f"Judge model labels differ from requested model: {sorted(models)}")
        raw = final.get("result", "").strip()
        (out / "final.txt").write_text(raw, encoding="utf-8")
        if raw.startswith("```") and raw.endswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw), record


class OpenAIResponsesRubricJudge:


    def __init__(self):
        self.model = os.environ.get("GAMEBENCH_VLM_MODEL", "gpt-5.6-sol")
        self.effort = os.environ.get("GAMEBENCH_VLM_EFFORT", "high")
        self.base_url = (os.environ.get("GAMEBENCH_VLM_BASE_URL")
                         or os.environ.get("OPENAI_BASE_URL")
                         or os.environ.get("AUTO_CODE_BASE_URL")
                         or "https://vip.auto-code.net")
        self.key_env = os.environ.get("GAMEBENCH_VLM_KEY_ENV", "AUTO_CODE_API_KEY")

    def validate_configuration(self):
        key = (os.environ.get(self.key_env, "") or os.environ.get("OPENAI_API_KEY", "")
               or os.environ.get("AUTO_CODE_API_KEY", ""))
        if not key:
            raise RuntimeError(f"VLM evaluation needs a key in {self.key_env}, OPENAI_API_KEY or AUTO_CODE_API_KEY")
        return key

    def score(self, prompt, images, out):
        from .judge import (
            _extract_json_object,
            _responses_api_url,
            default_openai_responses_requester,
            extract_responses_text,
        )
        from ..taskgen.package import write_json

        key = self.validate_configuration()
        out = Path(out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        (out / "prompt.md").write_text(prompt, encoding="utf-8")
        content = [{"type": "input_text", "text": prompt}]
        for item in images:
            path = Path(item["path"])
            media_type = mimetypes.guess_type(path)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.extend([
                {"type": "input_text", "text": str(item.get("label") or path.name)},
                {"type": "input_image", "image_url": f"data:{media_type};base64,{encoded}", "detail": "high"},
            ])
        payload = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 8000,
            "text": {"format": {"type": "json_object"}},
            "input": [{"role": "user", "content": content}],
        }
        if self.effort in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            payload["reasoning"] = {"effort": self.effort}
        endpoint = _responses_api_url(self.base_url)
        started = time.monotonic()
        with _serial_lock(Path(tempfile.gettempdir()) / "gamebench-vlm-serial.lock"):
            response = default_openai_responses_requester(payload, key, endpoint)
        raw = extract_responses_text(response)
        data = _extract_json_object(raw)
        served_model = str(response.get("model") or "") if isinstance(response, dict) else ""
        record = {
            "transport": "openai_responses",
            "provider": self.base_url,
            "endpoint": endpoint,
            "requested_model": self.model,
            "returned_models": [served_model] if served_model else [],
            "elapsed_s": round(time.monotonic() - started, 2),
            "attached_images": images,
            "raw_response": str(out / "result.json"),
        }
        write_json(out / "result.json", response)
        write_json(out / "request.json", record)
        (out / "final.txt").write_text(raw, encoding="utf-8")
        return data, record


def rubric_judge_from_env():
    provider = (os.environ.get("GAMEBENCH_VLM_PROVIDER") or "responses").strip().lower()
    if provider in {"responses", "openai", "openai_responses"}:
        return OpenAIResponsesRubricJudge()
    if provider in {"anthropic", "messages", "anthropic_messages", "claude_code"}:
        return ClaudeCodeRubricJudge()
    raise RuntimeError(f"Unsupported GAMEBENCH_VLM_PROVIDER: {provider!r}")
