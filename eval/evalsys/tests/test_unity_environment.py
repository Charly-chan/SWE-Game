from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from evalsys.taskgen.unity.unity_environment import (
    UnityEnvironmentProfile,
    detect_unity_environment,
    load_environment_profile,
)




def _community_profile() -> UnityEnvironmentProfile:
    return UnityEnvironmentProfile(
        profile_id="mode5-community-docker-v1",
        environment_class="community-docker",
        platform="linux",
        image_digest="sha256:fixture-image",
        unity_changeset="fixture-changeset",
        display="xvfb",
        renderer="mesa-llvmpipe",
        network_policy="candidate-and-evaluator-offline",
        limits={"cpus": 4, "memory_mb": 8192, "wall_seconds": 1800},
        license_mechanism="file",
        preflight_passed=True,
        paper_compatible=False,
    )


class UnityEnvironmentTests(unittest.TestCase):
    def test_complete_community_profile_can_execute_untrusted_code(self) -> None:
        self.assertTrue(_community_profile().ready_for_untrusted_execution)

    def test_incomplete_community_profile_is_refused(self) -> None:
        profile = UnityEnvironmentProfile(
            profile_id="incomplete", environment_class="community-docker", platform="linux"
        )
        self.assertFalse(profile.ready_for_untrusted_execution)
        self.assertIn("preflight_passed", profile.readiness_errors)
        self.assertIn("image_digest", profile.readiness_errors)

    def test_wsl_is_not_the_evaluator(self) -> None:
        with mock.patch("platform.system", return_value="Linux"), mock.patch.dict(
            "os.environ", {"WSL_INTEROP": "/run/WSL/1_interop"}, clear=True
        ):
            profile = detect_unity_environment()
        self.assertEqual("local-wsl-dev", profile.environment_class)
        self.assertFalse(profile.ready_for_untrusted_execution)

    def test_committed_community_profile_is_not_an_active_preflight(self) -> None:
        profile_path = (
            Path(__file__).resolve().parents[2]
            / "infra" / "unity" / "profiles" / "mode5-community-docker-v1.json"
        )
        profile = load_environment_profile(profile_path)
        self.assertEqual("community-docker", profile.environment_class)
        self.assertFalse(profile.paper_compatible)
        self.assertFalse(profile.ready_for_untrusted_execution)


if __name__ == "__main__":
    unittest.main()
