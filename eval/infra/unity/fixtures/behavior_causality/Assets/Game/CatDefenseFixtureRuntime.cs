using System;
using System.Collections;
using GameBenchmark;
using UnityEngine;
using UnityEngine.InputSystem;

namespace GameBench.Fixtures.CatDefense
{
    // Ordinary gameplay for evaluator calibration. This component never opens
    // the controller socket and never emits semantic rows or verdicts.
    public sealed class CatDefenseFixtureRuntime : MonoBehaviour
    {
        public PlayerInput playerInput;
        public GameObject enemy;
        public GameObject projectile;
        public GameObject goal;
        private InputAction action;
        private string variant;
        private string scenario;
        private int investments;
        private int coins;
        private bool fired;

        private void Start()
        {
            variant = ReadArgument("--gb-fixture-variant=");
            scenario = ReadArgument("--gb-calibration-scenario=");
            coins = scenario == "C3" ? 62 : 46;
            GBTelemetry.ReportNumeric("health", 140);
            GBTelemetry.ReportNumeric("score", 0);
            GBTelemetry.ReportNumeric("coins", coins);
            GBOutcome.ReportCheckpoint("spawn");
            action = playerInput.actions.FindAction("gb_action", true);
            action.performed += OnFire;
            action.Enable();
            if (variant == "auto_win") StartCoroutine(AutoWin());
            if (variant == "enemy_auto_disappears") StartCoroutine(AutoRemoveEnemy());
        }

        private void OnFire(InputAction.CallbackContext context)
        {
            if (fired || context.ReadValue<float>() <= 0 || variant == "input_ignored" ||
                variant == "visual_only" || variant == "auto_win") return;
            if (variant == "witness_only" &&
                Environment.CommandLine.IndexOf("witness_candidate", StringComparison.Ordinal) < 0) return;
            if ((scenario == "C1" || scenario == "C3") && investments < 2)
            {
                investments++;
                coins -= 22;
                GBTelemetry.ReportNumeric("coins", coins);
                GBOutcome.ReportCheckpoint("pad_investment_" + investments);
                if (investments == 1)
                {
                    playerInput.transform.position = new Vector3(-1, 0, 0);
                    return;
                }
            }
            fired = true;
            if (variant == "telemetry_fake")
            {
                GBTelemetry.ReportNumeric("score", 999);
                return;
            }
            projectile.transform.position = playerInput.transform.position;
            projectile.SetActive(true);
            StartCoroutine(ResolvePhysicalShot());
        }

        private IEnumerator ResolvePhysicalShot()
        {
            for (var frame = 0; frame < 90 && enemy != null && !Touching(projectile, enemy); frame++)
            {
                projectile.transform.position += Vector3.right * 0.25f;
                yield return new WaitForFixedUpdate();
            }
            if (enemy == null || !Touching(projectile, enemy)) yield break;
            GBOutcome.ReportCheckpoint("projectile_contact");
            yield return new WaitForFixedUpdate();
            Destroy(enemy);
            GBTelemetry.ReportNumeric("score", scenario == "C3" ? 90 : 100);
            GBOutcome.ReportCheckpoint("enemy_removed");
            for (var frame = 0; frame < 90 && !Touching(playerInput.gameObject, goal); frame++)
            {
                playerInput.transform.position += Vector3.right * 0.25f;
                yield return new WaitForFixedUpdate();
            }
            if (!Touching(playerInput.gameObject, goal)) yield break;
            GBOutcome.ReportCheckpoint("extraction_contact");
            GBOutcome.ReportSuccess();
        }

        private IEnumerator AutoRemoveEnemy()
        {
            for (var frame = 0; frame < 12; frame++) yield return new WaitForFixedUpdate();
            if (enemy != null) Destroy(enemy);
            GBOutcome.ReportSuccess();
        }

        private IEnumerator AutoWin()
        {
            for (var frame = 0; frame < 12; frame++) yield return new WaitForFixedUpdate();
            GBOutcome.ReportSuccess();
        }

        private static bool Touching(GameObject left, GameObject right)
        {
            if (left == null || right == null) return false;
            var a = left.GetComponent<Collider>();
            var b = right.GetComponent<Collider>();
            return a != null && b != null && Physics.ComputePenetration(
                a, a.transform.position, a.transform.rotation,
                b, b.transform.position, b.transform.rotation, out _, out _);
        }

        private static string ReadArgument(string prefix)
        {
            foreach (var value in Environment.GetCommandLineArgs())
                if (value.StartsWith(prefix, StringComparison.Ordinal)) return value.Substring(prefix.Length);
            return "";
        }

        private void OnGUI()
        {
            GUI.Box(new Rect(190, 150, 580, 240), "Behavior causality — physical calibration");
            GUI.Label(new Rect(340, 215, 360, 28), enemy == null ? "HOSTILES 0" : "HOSTILES 1");
            GUI.Label(new Rect(340, 250, 360, 28), "Observer owns the evidence");
            GUI.Label(new Rect(340, 285, 360, 28), "variant: " + variant);
        }

        private void OnDestroy()
        {
            if (action != null) action.performed -= OnFire;
        }
    }
}
