from __future__ import annotations

import copy
import unittest
from pathlib import Path

from evalsys.taskgen.unity.unity_vm_contract import validate_vm_job_contract


def _valid_contract() -> dict:
    return {
        "environment_profile": "profiles/ubuntu2404-unity6000.3.23f1.json",
        "candidate_input": {
            "host_path": "D:\\gamebench-unity-vm\\runtime\\candidate-input.iso",
            "guest_mount": "/run/gamebench/candidate-input",
            "copy_to": "/var/lib/gamebench/unity-scratch/project",
            "read_only": True,
        },
        "controller_input": {
            "host_path": "D:\\gamebench-unity-vm\\runtime\\controller-input.iso",
            "guest_mount": "/run/gamebench/controller-input",
            "read_only": True,
        },
        "output_disk": {
            "host_path": "D:\\gamebench-unity-vm\\runtime\\output.raw",
            "guest_mount": "/run/gamebench/output",
            "new_blank": True,
            "format": "raw-ext4",
            "size_mb": 128,
        },
        "users": {
            "unity_runner": "unity-runner",
            "gb_controller": "gb-controller",
            "distinct_uids": True,
        },
        "isolation": {
            "host_mounts": [],
            "candidate_network": "none",
            "controller_network": "loopback-only",
            "mount_namespace": True,
            "hidepid": True,
            "ptrace_restricted": True,
            "cgroup_limits": True,
            "apparmor_profile": "gamebench-unity-runner",
            "unity_api_key_present": False,
            "license_state_network": "none",
            "same_vm_editor_and_player": True,
            "limits": {
                "cpus": 8,
                "memory_mb": 12288,
                "disk_mb": 65536,
                "processes": 512,
                "wall_seconds": 1800,
            },
        },
        "lifecycle": {
            "offline_artifact_read": True,
            "destroy_clone": True,
            "destroy_output_disk": True,
            "preserve_only_manifested_artifacts": True,
        },
    }


class UnityVMContractTests(unittest.TestCase):
    def test_sandbox_fixture_handoff_uses_generic_causality_name(self) -> None:
        unity_root = Path(__file__).resolve().parents[2] / "infra" / "unity"
        host = (unity_root / "host_runner" / "Invoke-UnitySandboxCertification.ps1").read_text()
        guest = (unity_root / "guest_runner" / "run-sandbox-certification.sh").read_text()
        artifact = "behavior-causality-fixture.tar.gz"
        self.assertIn(artifact, host)
        self.assertIn(artifact, guest)
        self.assertNotIn("cat-fixture.tar.gz", guest)

    def test_complete_separated_contract_passes(self) -> None:
        self.assertTrue(validate_vm_job_contract(_valid_contract()).ok)

    def test_shared_input_and_host_mounts_fail(self) -> None:
        contract = _valid_contract()
        contract["controller_input"]["host_path"] = contract["candidate_input"]["host_path"]
        contract["isolation"]["host_mounts"] = ["D:\\repo"]
        report = validate_vm_job_contract(contract)
        self.assertFalse(report.ok)
        self.assertIn("candidate, controller, and output disks must be separate artifacts", report.errors)
        self.assertIn("host mounts are forbidden", report.errors)

    def test_each_security_control_fails_closed(self) -> None:
        mutations = (
            ("candidate_input", "read_only", False),
            ("controller_input", "read_only", False),
            ("output_disk", "new_blank", False),
            ("users", "distinct_uids", False),
            ("isolation", "candidate_network", "external"),
            ("isolation", "controller_network", "external"),
            ("isolation", "mount_namespace", False),
            ("isolation", "hidepid", False),
            ("isolation", "ptrace_restricted", False),
            ("isolation", "cgroup_limits", False),
            ("isolation", "unity_api_key_present", True),
            ("isolation", "license_state_network", "external"),
            ("isolation", "same_vm_editor_and_player", False),
            ("lifecycle", "offline_artifact_read", False),
            ("lifecycle", "destroy_clone", False),
        )
        for section, key, replacement in mutations:
            with self.subTest(section=section, key=key):
                contract = copy.deepcopy(_valid_contract())
                contract[section][key] = replacement
                self.assertFalse(validate_vm_job_contract(contract).ok)

    def test_wsl_or_host_interop_candidate_scratch_fails(self) -> None:
        contract = _valid_contract()
        contract["candidate_input"]["copy_to"] = "/mnt/d/game-intake"
        report = validate_vm_job_contract(contract)
        self.assertFalse(report.ok)
        self.assertIn(
            "candidate scratch must not use /mnt host interoperability paths",
            report.errors,
        )

    def test_relative_or_user_profile_host_paths_fail(self) -> None:
        contract = _valid_contract()
        contract["candidate_input"]["host_path"] = "candidate-input.iso"
        self.assertFalse(validate_vm_job_contract(contract).ok)
        contract = _valid_contract()
        contract["controller_input"]["host_path"] = "C:\\Users\\owner\\hidden.iso"
        self.assertFalse(validate_vm_job_contract(contract).ok)

    def test_missing_resource_limit_fails(self) -> None:
        contract = _valid_contract()
        contract["isolation"]["limits"]["processes"] = 0
        report = validate_vm_job_contract(contract)
        self.assertIn("isolation.limits.processes must be positive", report.errors)

    def test_malformed_output_capacity_fails_closed(self) -> None:
        contract = _valid_contract()
        contract["output_disk"]["size_mb"] = {"not": "a number"}
        report = validate_vm_job_contract(contract)
        self.assertIn("output disk must declare a positive raw-ext4 capacity", report.errors)


if __name__ == "__main__":
    unittest.main()
