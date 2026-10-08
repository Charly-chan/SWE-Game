


from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_SANDBOX_IMAGE = os.environ.get("GB_SANDBOX_IMAGE") or "gamebench-agent:godot-4.5.1"
DOCKER_BIN = os.environ.get("GB_DOCKER_BIN") or "docker"


CONTAINER_WORKSPACE = "/workspace"


CONTAINER_HOME = "/opt/gb-agent-home"
CONTAINER_BIN = "/opt/gb-agent-bin"


LABEL_SANDBOX = "gamebench.sandbox"
LABEL_CELL = "gamebench.cell"
LABEL_PID = "gamebench.pid"


START_TIMEOUT_S = int(os.environ.get("GB_SANDBOX_START_TIMEOUT_S") or 600)
DOCKER_CALL_TIMEOUT_S = 300


CONTAINER_ENV_KEYS = (
    "PATH",
    "HOME",
    "GODOT_BIN",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "GB_TASK_WORKSPACE",
    "GB_TASK_PROMPT",
    "GB_TASK_SUBMISSION",
    "GB_SANDBOX_IMAGE_ID",
    "GB_REAL_TIMEOUT",
    "GB_CHILD_TIMEOUT_CAP_S",
    "LLM_MODEL",
)


class DockerSandboxError(RuntimeError):
    pass


def client_env() -> dict[str, str]:


    env = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    }
    env["DOCKER_CLI_HINTS"] = "false"
    return env


@dataclass(frozen=True)
class ContainerFacts:


    login_path: str

    paths: Mapping[str, str] = field(default_factory=dict)
    versions: Mapping[str, str | None] = field(default_factory=dict)
    tools: Mapping[str, bool] = field(default_factory=dict)
    uname: str = ""
    home_writable: bool = False

    def path_of(self, name: str) -> str | None:
        return self.paths.get(name) or None


_PROBE_PY = r"""
import json, os, shutil, subprocess, sys

request = json.loads(sys.stdin.read())
tools = request["tools"]
versioned = request["versioned"]
home = request["home"]


def which(name):
    return shutil.which(name)


def version(argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    text = (proc.stdout or proc.stderr or "").strip()
    return text.splitlines()[0] if text else None


def login_path():
    for shell in ("bash", "sh"):
        exe = shutil.which(shell)
        if not exe:
            continue
        try:
            proc = subprocess.run(
                [exe, "-lc", 'printf %s "$PATH"'],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return os.environ.get("PATH", "")


paths = {}
for name in set(tools) | set(versioned) | {"timeout", "bash", "sh"}:
    found = which(name)
    if found:
        paths[name] = found

versions = {}
for name, argv in versioned.items():
    versions[name] = version([paths[name]] + argv) if name in paths else None

print(json.dumps({
    "login_path": login_path(),
    "paths": paths,
    "versions": versions,
    "tools": {name: name in paths for name in tools},
    "uname": " ".join(os.uname()),
    "home_writable": bool(os.path.isdir(home) and os.access(home, os.W_OK)),
}))
"""


def render_env_file(values: Mapping[str, str]) -> str:


    lines = []
    for key, value in sorted(values.items()):
        if not key or "=" in key:
            raise DockerSandboxError(f"invalid environment variable name {key!r}")
        for bad, label in (("\n", "newline"), ("\r", "carriage return"), ("\0", "NUL")):
            if bad in key or bad in str(value):
                raise DockerSandboxError(
                    f"environment variable {key!r} contains a {label}, which "
                    "docker --env-file cannot represent"
                )
        lines.append(f"{key}={value}")
    return "".join(f"{line}\n" for line in lines)


class DockerSandbox:


    def __init__(self, *, image: str, log_path: Path, cell: str) -> None:
        self.image = image
        self.log_path = log_path
        self.cell = cell
        self.name = f"gb-agent-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.client_env = client_env()
        self.image_id: str | None = None
        self.facts: ContainerFacts | None = None
        self.transfer: dict[str, Any] = {}
        self._docker: str | None = None
        self._started = False
        self._closed = False
        self._env_file: Path | None = None


    def _log(self, text: str) -> None:


        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(text if text.endswith("\n") else text + "\n")
        except OSError:
            pass

    def _run(
        self,
        args: Sequence[str],
        *,
        stdin: str | None = None,
        timeout: int = DOCKER_CALL_TIMEOUT_S,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        argv = [self.docker, *args]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                input=stdin,
                timeout=timeout,
                env=self.client_env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._log(f"$ {' '.join(argv)}\n-> timed out after {timeout}s")
            raise DockerSandboxError(
                f"docker command timed out after {timeout}s: {' '.join(args[:2])}"
            ) from exc
        except OSError as exc:
            raise DockerSandboxError(f"could not run docker: {exc}") from exc
        if proc.returncode != 0:
            self._log(
                f"$ {' '.join(argv)}\n-> exit {proc.returncode}\n"
                f"{proc.stdout}{proc.stderr}"
            )
            if check:
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()
                raise DockerSandboxError(
                    f"docker {args[0]} failed with status {proc.returncode}: "
                    + (detail[-1] if detail else "no output")
                )
        return proc

    @property
    def docker(self) -> str:


        if self._docker is None:
            found = shutil.which(DOCKER_BIN, path=os.environ.get("PATH"))
            if not found:
                raise DockerSandboxError(
                    f"docker client {DOCKER_BIN!r} was not found on PATH"
                )
            self._docker = found
        return self._docker

    def exec_argv(
        self,
        args: Sequence[str],
        *,
        interactive: bool = False,
        env_file: Path | None = None,
        workdir: str = CONTAINER_WORKSPACE,
    ) -> list[str]:
        argv = [self.docker, "exec"]
        if interactive:
            argv.append("-i")
        argv += ["-w", workdir, "--user", f"{os.getuid()}:{os.getgid()}"]
        if env_file is not None:
            argv += ["--env-file", str(env_file)]
        argv.append(self.name)
        return argv + list(args)

    def _exec(
        self,
        args: Sequence[str],
        *,
        stdin: str | None = None,
        timeout: int = DOCKER_CALL_TIMEOUT_S,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        argv = self.exec_argv(args, interactive=stdin is not None)[1:]
        return self._run(argv, stdin=stdin, timeout=timeout, check=check)


    def start(self) -> None:
        version = self._run(
            ["version", "--format", "{{.Server.Version}}"], timeout=60, check=False
        )
        if version.returncode != 0:
            raise DockerSandboxError(
                "the docker daemon did not respond (DOCKER_HOST="
                f"{os.environ.get('DOCKER_HOST') or '<unset>'}); "
                "--agent-sandbox docker needs a reachable daemon"
            )
        self._log(f"daemon {version.stdout.strip()} image {self.image}")


        self._run(
            [
                "run", "-d", "--name", self.name,
                "--label", f"{LABEL_SANDBOX}=agent",
                "--label", f"{LABEL_CELL}={self.cell}",
                "--label", f"{LABEL_PID}={os.getpid()}",
                self.image, "sleep", "infinity",
            ],
            timeout=START_TIMEOUT_S,
        )
        self._started = True
        self._await_running()
        self._record_image()
        self._bootstrap()

    def _await_running(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            state = self._run(
                ["inspect", "-f", "{{.State.Running}}", self.name],
                timeout=60,
                check=False,
            )
            if state.returncode == 0 and state.stdout.strip() == "true":


                if self._run(["exec", self.name, "true"], timeout=60, check=False).returncode == 0:
                    return
            time.sleep(2)
        raise DockerSandboxError(
            f"sandbox container {self.name} was not ready within {START_TIMEOUT_S}s "
            f"(image {self.image}; a cold pull may need longer -- raise "
            "GB_SANDBOX_START_TIMEOUT_S)"
        )

    def _record_image(self) -> None:
        digest = self._run(
            ["image", "inspect", "--format", "{{index .RepoDigests 0}}", self.image],
            timeout=60,
            check=False,
        )
        if digest.returncode == 0 and digest.stdout.strip():
            self.image_id = digest.stdout.strip()
            return
        ident = self._run(
            ["inspect", "--format", "{{.Image}}", self.name], timeout=60, check=False
        )
        self.image_id = ident.stdout.strip() or None

    def _bootstrap(self) -> None:


        script = (
            f"mkdir -p {CONTAINER_WORKSPACE} {CONTAINER_HOME}/codex "
            f"{CONTAINER_HOME}/claude {CONTAINER_BIN} && "
            f"chown -R {os.getuid()}:{os.getgid()} {CONTAINER_WORKSPACE} "
            f"{CONTAINER_HOME} {CONTAINER_BIN}"
        )
        self._run(["exec", "--user", "0:0", self.name, "sh", "-c", script], timeout=120)

    def probe_facts(
        self,
        *,
        tools: Iterable[str],
        versioned: Mapping[str, list[str]],
        home: str = CONTAINER_HOME,
    ) -> ContainerFacts:

        if self.facts is not None:
            return self.facts
        request = json.dumps(
            {"tools": list(tools), "versioned": dict(versioned), "home": home}
        )


        proc = self._exec(
            ["python3", "-c", _PROBE_PY], stdin=request, timeout=180, check=False
        )
        if proc.returncode != 0:
            raise DockerSandboxError(
                "could not probe the sandbox image: "
                + (proc.stderr or proc.stdout or "").strip()[-400:]
            )
        try:
            wire = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise DockerSandboxError(
                f"sandbox probe returned unparseable output: {proc.stdout[:400]!r}"
            ) from exc
        self.facts = ContainerFacts(
            login_path=str(wire.get("login_path") or ""),
            paths=dict(wire.get("paths") or {}),
            versions=dict(wire.get("versions") or {}),
            tools=dict(wire.get("tools") or {}),
            uname=str(wire.get("uname") or ""),
            home_writable=bool(wire.get("home_writable")),
        )
        self._log(f"probe {json.dumps(wire, sort_keys=True)}")
        return self.facts

    def which(self, name: str, _env: Mapping[str, str] | None = None) -> str | None:


        if self.facts is not None:
            found = self.facts.path_of(name)
            if found:
                return found
        proc = self._exec(["sh", "-lc", f"command -v {name}"], timeout=60, check=False)
        resolved = proc.stdout.strip().splitlines()
        return resolved[-1] if proc.returncode == 0 and resolved else None

    def install_child_timeout_shim(self, *, source: str, cap_s: int) -> tuple[str, str]:


        facts = self.facts
        if facts is None:
            raise DockerSandboxError("probe the sandbox before installing the shim")
        real_timeout = facts.path_of("timeout")
        if not real_timeout:
            raise DockerSandboxError(
                "the docker coding-agent sandbox requires GNU timeout in the image"
            )
        shim_dirs = [CONTAINER_BIN, "/usr/local/bin"]
        real_dir = str(Path(real_timeout).parent)
        entries = [entry for entry in facts.login_path.split(":") if entry]

        if real_dir in entries:
            real_index = entries.index(real_dir)
            if not any(
                candidate in entries and entries.index(candidate) < real_index
                for candidate in shim_dirs
            ):
                raise DockerSandboxError(
                    f"neither {CONTAINER_BIN} nor /usr/local/bin precedes "
                    f"{real_dir} in the sandbox login PATH ({facts.login_path}); "
                    "the child-timeout cap would not take effect"
                )
        with tempfile.TemporaryDirectory() as staging:
            shim = Path(staging) / "timeout"
            shim.write_text(source, encoding="utf-8")
            shim.chmod(0o755)
            for target in shim_dirs:
                self._run(["cp", str(shim), f"{self.name}:{target}/timeout"], timeout=120)
                self._run(
                    ["exec", "--user", "0:0", self.name, "chmod", "755", f"{target}/timeout"], timeout=60
                )
        self._log(f"child timeout cap {cap_s}s via {shim_dirs} -> {real_timeout}")
        return CONTAINER_BIN, real_timeout

    def install_godot(self, source: Path, *, want_prefix: str) -> str:


        target = "/usr/local/bin/godot"
        with tempfile.TemporaryDirectory() as staging:
            local = Path(staging) / "godot"
            shutil.copyfile(source, local)
            local.chmod(0o755)
            self._run(["cp", str(local), f"{self.name}:{target}"], timeout=300)
        self._run(["exec", "--user", "0:0", self.name, "chmod", "755", target], timeout=60)
        seen = self._exec([target, "--version"], timeout=120, check=False)
        reported = (seen.stdout or seen.stderr or "").strip().splitlines()
        first = reported[0] if reported else ""
        if not first.startswith(want_prefix):
            raise DockerSandboxError(
                f"injected Godot reports {first!r} in the sandbox, expected "
                f"{want_prefix}*; refusing to run with an engine that is neither "
                "the image's nor the evaluator's"
            )
        self._log(f"godot {first} injected at {target} (was the image's build)")

        self.facts = None
        return target

    def write_env_file(self, values: Mapping[str, str]) -> Path:
        handle = tempfile.NamedTemporaryFile(
            "w", prefix="gb-sandbox-env-", delete=False, encoding="utf-8"
        )
        try:
            handle.write(render_env_file(values))
        finally:
            handle.close()
        path = Path(handle.name)
        path.chmod(0o600)
        self._env_file = path
        return path

    def copy_in(self, host_dir: Path) -> None:
        self._run(["cp", f"{host_dir}{os.sep}.", f"{self.name}:{CONTAINER_WORKSPACE}"])


        self._run(["exec", "--user", "0:0", self.name, "chown", "-R",
                   f"{os.getuid()}:{os.getgid()}", CONTAINER_WORKSPACE], timeout=120)
        check = self._exec(
            ["test", "-f", f"{CONTAINER_WORKSPACE}/PROMPT.md"], timeout=60, check=False
        )
        if check.returncode != 0:
            raise DockerSandboxError(
                f"PROMPT.md is missing from {CONTAINER_WORKSPACE} after copy-in"
            )
        self.transfer["in"] = _tree_stats(host_dir)

    def copy_out(self, host_dir: Path) -> None:


        host_dir = host_dir.resolve()
        staging = host_dir.parent / f".{host_dir.name}.incoming"
        stale = host_dir.parent / f".{host_dir.name}.stale"
        for path in (staging, stale):
            shutil.rmtree(path, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            self._run(["cp", f"{self.name}:{CONTAINER_WORKSPACE}", str(staging)])
            produced = staging / Path(CONTAINER_WORKSPACE).name
            if not produced.is_dir():
                raise DockerSandboxError(
                    f"docker cp produced no {CONTAINER_WORKSPACE} directory"
                )
            self.transfer["out"] = _tree_stats(produced)
            if host_dir.exists():
                os.replace(host_dir, stale)
            os.replace(produced, host_dir)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(stale, ignore_errors=True)

    def close(self, *, copy_out_to: Path | None = None) -> Exception | None:


        if self._closed:
            return None
        self._closed = True
        failure: Exception | None = None
        if copy_out_to is not None and self._started:
            try:
                self.copy_out(copy_out_to)
            except (DockerSandboxError, OSError) as exc:
                failure = exc
                self._log(f"copy-out failed: {exc}")
        if self._started:
            self._run(["rm", "-f", "-v", self.name], timeout=120, check=False)
        if self._env_file is not None:
            try:
                self._env_file.unlink()
            except OSError:
                pass
        return failure


def _tree_stats(root: Path) -> dict[str, int]:
    files = 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            files += 1
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return {"files": files, "bytes": total}
