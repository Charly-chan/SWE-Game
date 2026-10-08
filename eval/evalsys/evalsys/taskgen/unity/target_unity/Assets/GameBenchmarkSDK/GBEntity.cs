using System;
using UnityEngine;

namespace GameBenchmark
{
    /// <summary>Public semantic locator. The evaluator observes the marked Unity object.</summary>
    [DisallowMultipleComponent]
    public sealed class GBEntity : MonoBehaviour
    {
        [SerializeField] private string role = "gb_player";
        [SerializeField] private string stableId = "";

        public string Role => role;
        public string StableId => stableId;

        /// <summary>
        /// Configure a marker created by ordinary runtime gameplay. Candidate
        /// code already owns serialized marker values; this API merely avoids
        /// reflection when pooled or procedurally spawned entities appear.
        /// </summary>
        public void Configure(string semanticRole, string id)
        {
            role = (semanticRole ?? "").Trim().ToLowerInvariant();
            stableId = (id ?? "").Trim();
            if (String.IsNullOrWhiteSpace(role))
                throw new ArgumentException("semantic role is required", nameof(semanticRole));
        }

        private void OnValidate()
        {
            role = (role ?? "").Trim().ToLowerInvariant();
            stableId = (stableId ?? "").Trim();
        }
    }
}
