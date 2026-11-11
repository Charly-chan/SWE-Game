import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

from evalsys.taskgen.mode5.community_profile import load_default_profile
from evalsys.taskgen.mode5.docker_environment import (
    CommunityDockerError, DockerClient, assert_offline_networks, ensure_archives,
    export_manual_activation_request, run_doctor,
)
from evalsys.taskgen.mode5.docker_license import LicenseProvider


IMAGE_ID = "sha256:" + "d" * 64


def test_docker_output_is_decoded_as_utf8_on_non_utf8_windows_hosts():
    observed = {}

    def fake_run(argv, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    DockerClient(executable="docker", run_command=fake_run).run(["version"])
    assert observed["encoding"] == "utf-8"
    assert observed["errors"] == "replace"


def test_network_inspection_accepts_only_docker_none_placeholder():
    assert_offline_networks("{}")
    assert_offline_networks('{"none":{"Gateway":"","IPAddress":""}}')
    import pytest
    with pytest.raises(CommunityDockerError, match="attached network"):
        assert_offline_networks('{"bridge":{"Gateway":"172.17.0.1"}}')


def test_archive_copy_reaches_tmpfs_without_docker_cp(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("input", encoding="utf-8")
    observed = {}

    def fake_subprocess(argv, **kwargs):
        assert argv[1:3] == ["exec", "-i"]
        with tarfile.open(fileobj=kwargs["stdin"], mode="r:") as archive:
            observed["members"] = archive.getnames()
            observed["payload"] = archive.extractfile("input.txt").read()
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(
        "evalsys.taskgen.mode5.docker_environment.subprocess.run", fake_subprocess,
    )
    DockerClient("docker").copy_into_directory(source, "candidate", "/tmpfs")
    assert observed == {"members": ["input.txt"], "payload": b"input"}


def test_archive_copy_from_tmpfs_extracts_regular_files(tmp_path, monkeypatch):
    def fake_subprocess(argv, **kwargs):
        assert argv[1:3] == ["exec", "--user"]
        payload = b"report"
        info = tarfile.TarInfo("./report.json")
        info.size = len(payload)
        with tarfile.open(fileobj=kwargs["stdout"], mode="w") as archive:
            archive.addfile(info, io.BytesIO(payload))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(
        "evalsys.taskgen.mode5.docker_environment.subprocess.run", fake_subprocess,
    )
    destination = tmp_path / "evaluation"
    DockerClient("docker").copy_from_directory("evaluator", "/output", destination)
    assert (destination / "report.json").read_bytes() == b"report"


def test_manual_activation_request_is_offline_and_bound_to_locked_image(tmp_path):
    class FakeDocker:
        def __init__(self):
            self.calls = []

        def image_id(self, image):
            return IMAGE_ID

        def run(self, args, *, timeout=600, check=True):
            self.calls.append(list(args))
            stdout = ""
            if args[:3] == ["inspect", "--format", "{{json .NetworkSettings.Networks}}"]:
                stdout = '{"none":{"Gateway":"","IPAddress":""}}\n'
            elif len(args) >= 4 and args[:3] == ["exec", "--user", "0:0"] and "find" in args:
                stdout = "Unity_v6000.3.23f1.alf\n"
            return subprocess.CompletedProcess(args, 0, stdout, "")

        def copy_from_directory(self, container, source, destination, *, timeout=600):
            self.calls.append(["archive-out", container, source, str(destination)])
            (Path(destination) / "Unity_v6000.3.23f1.alf").write_bytes(b"a" * 832)

    docker = FakeDocker()
    out = tmp_path / "request.alf"
    result = export_manual_activation_request(
        image_lock={"agent_image": {"tag": "agent:test", "id": IMAGE_ID}},
        out=out, docker=docker,
    )
    assert result == out.resolve()
    assert out.stat().st_size == 832
    create = next(call for call in docker.calls if call[:1] == ["create"])
    assert create[create.index("--network") + 1] == "none"
    assert "--read-only" in create
    assert any(call[:2] == ["rm", "-f"] for call in docker.calls)


class FakeDoctorDocker:
    def __init__(self):
        self.calls = []

    def image_id(self, image):
        self.calls.append(("image_id", image))
        return IMAGE_ID

    def run(self, args, *, timeout=600, check=True):
        args = list(args)
        self.calls.append(args)
        rc, stdout = 0, ""
        if args[:3] == ["image", "inspect", "--format"]:
            stdout = "linux/amd64\n"
        elif args[:3] == ["inspect", "--format", "{{json .NetworkSettings.Networks}}"]:
            stdout = "{}\n"
        elif args[:3] == ["exec", args[1] if len(args) > 1 else "", "unity"] and "-version" in args:
            stdout = "6000.3.23f1 (09d2ecc7fb28)\n"
        elif args[:1] == ["cp"] and args[-1].endswith(":/work/fixture/"):
            staged = Path(args[1][:-2])
            lock = staged / "Packages" / "packages-lock.json"
            self.staged_lock_digest = hashlib.sha256(lock.read_bytes()).hexdigest()
            self.staged_manifest = (staged / "Packages/manifest.json").read_text(encoding="utf-8")
            self.staged_files = {
                "/work/fixture/" + path.relative_to(staged).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in staged.rglob("*") if path.is_file()
            }
        elif len(args) >= 4 and args[0] == "exec" and args[2] == "cat":
            stdout = self.staged_manifest
        elif len(args) >= 4 and args[0] == "exec" and args[2] == "sha256sum":
            stdout = "".join(f"{self.staged_files[path]}  {path}\n" for path in args[3:])
        elif len(args) >= 4 and args[0] == "exec" and args[2] == "find":
            stdout = "\n".join(path for path in self.staged_files if path.startswith(args[3] + "/"))
        elif args and args[0] == "inspect":
            rc = 1
        return subprocess.CompletedProcess(args, rc, stdout, "")


def test_doctor_runs_d01_through_d10_and_disconnects_floating_license(tmp_path):
    docker = FakeDoctorDocker()
    lock = {
        "agent_image": {"tag": "agent:test", "id": IMAGE_ID},
        "evaluator_image": {"tag": "evaluator:test", "id": IMAGE_ID},
    }
    report = run_doctor(
        repo_root=Path(__file__).parents[3], profile=load_default_profile(),
        image_lock=lock, license_provider=LicenseProvider.floating("http://license:8080"),
        docker=docker,
    )
    assert [row["id"] for row in report["checks"]] == [f"D{i:02d}" for i in range(1, 11)]
    creates = [call for call in docker.calls if isinstance(call, list) and call[:1] == ["create"]]
    assert len(creates) == 1
    assert "--network" in creates[0]
    assert creates[0][creates[0].index("--network") + 1] == "bridge"
    assert any(call[:3] == ["network", "disconnect", "bridge"]
               for call in docker.calls if isinstance(call, list))
    assert next(row for row in report["checks"] if row["id"] == "D09")["status"] == "pass"
    assert report["paper_compatible"] is False
    assert report["status"] == "pass"
    assert next(row for row in report["checks"] if row["id"] == "D06")["status"] == "pass"


def test_doctor_rejects_image_digest_drift():
    class DriftDocker(FakeDoctorDocker):
        def image_id(self, image):
            return "sha256:" + "e" * 64

    report = run_doctor(
        repo_root=Path(__file__).parents[3], profile=load_default_profile(),
        image_lock={
            "agent_image": {"tag": "agent:test", "id": IMAGE_ID},
            "evaluator_image": {"tag": "evaluator:test", "id": IMAGE_ID},
        },
        license_provider=LicenseProvider.floating("http://license:8080"),
        docker=DriftDocker(),
    )
    d03 = next(row for row in report["checks"] if row["id"] == "D03")
    assert d03["status"] == "fail"
    assert "digest mismatch" in d03["detail"]
    assert report["status"] == "fail"


def test_missing_archive_download_is_atomic_and_verified(tmp_path, monkeypatch):
    payload = b"official-unity-fixture"
    manifest = {"archives": {"editor": {
        "file": "Unity-fixture.tar.xz",
        "url": "https://download.unity3d.com/fixture",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }}}

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size=-1):
            nonlocal payload
            chunk, payload = payload, b""
            return chunk

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())
    result = ensure_archives(tmp_path, manifest)
    assert result["editor"]["bytes"] == len(b"official-unity-fixture")
    assert (tmp_path / "Unity-fixture.tar.xz").is_file()
    assert not (tmp_path / "Unity-fixture.tar.xz.part").exists()
