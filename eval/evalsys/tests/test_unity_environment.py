from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from unittest import mock

from evalsys.taskgen.unity.unity_environment import (
    UNITY_V2_CANDIDATE_VERSION,
    UnityEnvironmentProfile,
    detect_unity_environment,
    load_environment_profile,
)


def _certified_profile() -> UnityEnvironmentProfile:
    return UnityEnvironmentProfile(
        profile_id="ubuntu-24.04-unity-6000.3.23f1",
        environment_class="linux-vm-certified",
        platform="Ubuntu 24.04 x86_64",
        score_eligible=True,
        certified=True,
        certification_status="certified",
        graphical_profile="xvfb-mesa-llvmpipe-pinned",
        image_digest="sha256:fixture-image",
        guest_os="ubuntu-24.04-x86_64",
        kernel="6.8.0-fixture",
        hypervisor="hyper-v",
        unity_changeset="fixture-changeset",
        unity_modules=("linux-il2cpp",),
        package_lock_digest="sha256:fixture-packages",
        display="xvfb",
        renderer="mesa-llvmpipe",
        mesa_version="fixture-mesa",
        network_policy="loopback-only",
        limits={"cpus": 4, "memory_mb": 8192, "disk_mb": 32768, "wall_seconds": 1800},
        license_mechanism="isolated-license-service",
        certification_record_digest="sha256:fixture-certification",
        preflight_passed=True,
    )


class UnityEnvironmentTests(unittest.TestCase):
    def test_candidate_version_is_not_a_certification_claim(self) -> None:
        profile = UnityEnvironmentProfile(
            profile_id="candidate",
            environment_class="linux-vm-certified",
            platform="Ubuntu 24.04",
            editor_version=UNITY_V2_CANDIDATE_VERSION,
        )
        self.assertEqual("6000.3.23f1", profile.editor_version)
        self.assertFalse(profile.certified)
        self.assertFalse(profile.score_eligible)
        self.assertFalse(profile.ready_for_untrusted_execution)

    def test_wsl_is_development_only(self) -> None:
        with mock.patch("platform.system", return_value="Linux"), mock.patch.dict(
            "os.environ", {"WSL_INTEROP": "/run/WSL/1_interop"}, clear=True
        ):
            profile = detect_unity_environment()
        self.assertEqual("local-wsl-dev", profile.environment_class)
        self.assertFalse(profile.score_eligible)
        self.assertFalse(profile.ready_for_untrusted_execution)

    def test_only_complete_certified_vm_profile_can_execute_untrusted_code(self) -> None:
        profile = _certified_profile()
        self.assertTrue(profile.ready_for_untrusted_execution)

    def test_certified_label_without_preflight_or_full_profile_is_refused(self) -> None:
        profile = UnityEnvironmentProfile(
            profile_id="unsafe-shortcut",
            environment_class="linux-vm-certified",
            platform="Ubuntu",
            score_eligible=True,
            certified=True,
            certification_status="certified",
        )
        self.assertFalse(profile.ready_for_untrusted_execution)
        self.assertIn("preflight_passed", profile.readiness_errors)
        self.assertIn("base_image_digest".replace("base_", ""), profile.readiness_errors)

    def test_committed_profile_is_certified_by_the_pinned_record(self) -> None:
        profile_path = (
            Path(__file__).resolve().parents[2]
            / "infra" / "unity" / "profiles"
            / "ubuntu2404-unity6000.3.23f1.json"
        )
        profile = load_environment_profile(profile_path)
        certification_path = (
            profile_path.parent.parent
            / "evidence"
            / "environment-certification.json"
        )
        certification_digest = hashlib.sha256(certification_path.read_bytes()).hexdigest()
        self.assertEqual("certified", profile.certification_status)
        self.assertTrue(profile.score_eligible)
        self.assertTrue(profile.ready_for_untrusted_execution)
        self.assertEqual(
            "sha256:" + certification_digest,
            profile.certification_record_digest,
        )


if __name__ == "__main__":
    unittest.main()
