using System.Collections;
using GameBenchmark;
using UnityEngine;
using UnityEngine.InputSystem;

namespace GameBench.Fixtures.Observer
{
    public sealed class ObserverFixtureRuntime : MonoBehaviour
    {
        public PlayerInput playerInput;
        public GameObject enemy;
        public GameObject projectile;
        public GameObject goal;
        private bool fired;
        private InputAction action;

        private void Start()
        {
            GBTelemetry.ReportNumeric("health", 100);
            GBTelemetry.ReportNumeric("score", 0);
            GBTelemetry.ReportNumeric("input_performed", 0);
            GBOutcome.ReportCheckpoint("spawn");
            action = playerInput != null && playerInput.actions != null
                ? playerInput.actions.FindAction("gb_action", false)
                : null;
            if (action != null)
            {
                action.performed += OnActionPerformed;
                action.Enable();
            }
        }

        private void OnActionPerformed(InputAction.CallbackContext context)
        {
            if (fired || context.ReadValue<float>() <= 0) return;
            fired = true;
            GBTelemetry.ReportNumeric("input_performed", 1);
            projectile.transform.position = playerInput.transform.position;
            projectile.SetActive(true);
            StartCoroutine(ResolveHit());
        }

        private IEnumerator ResolveHit()
        {
            // Real geometry determines the hit. Merely receiving an input
            // command does not remove the enemy or produce a contact event.
            for (var i = 0; i < 90 && enemy != null && !Touching(projectile, enemy); i++)
            {
                projectile.transform.position += Vector3.right * 0.25f;
                yield return new WaitForFixedUpdate();
            }
            if (enemy == null || !Touching(projectile, enemy)) yield break;
            GBOutcome.ReportCheckpoint("projectile_contact");
            yield return new WaitForFixedUpdate();
            Destroy(enemy);
            GBTelemetry.ReportNumeric("score", 100);
            GBOutcome.ReportCheckpoint("enemy_removed");
            // This trusted conformance scene also exercises a real goal
            // contact after an initially separated player/goal pair.
            for (var i = 0; i < 90 && !Touching(playerInput.gameObject, goal); i++)
            {
                playerInput.transform.position += Vector3.right * 0.25f;
                yield return new WaitForFixedUpdate();
            }
            if (!Touching(playerInput.gameObject, goal)) yield break;
            yield return new WaitForFixedUpdate();
            GBOutcome.ReportSuccess();
        }

        private static bool Touching(GameObject a, GameObject b)
        {
            if (a == null || b == null) return false;
            var ca = a.GetComponent<Collider>();
            var cb = b.GetComponent<Collider>();
            return ca != null && cb != null && Physics.ComputePenetration(
                ca, ca.transform.position, ca.transform.rotation,
                cb, cb.transform.position, cb.transform.rotation, out var direction, out var distance);
        }

        private void OnDestroy()
        {
            if (action != null) action.performed -= OnActionPerformed;
        }
    }
}
