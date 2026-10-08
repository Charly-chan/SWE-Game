using System;
using System.Collections.Generic;
using UnityEngine;

namespace GameBenchmark
{
    /// <summary>
    /// Public, evaluator-versioned state surface for a GBEntity. Candidates use
    /// this component instead of inventing a manifest or reflection contract.
    /// State claims remain corroborating evidence and are checked against
    /// evaluator-observed geometry, lifecycle, inputs, and consequences.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class GBObservableState : MonoBehaviour
    {
        [Serializable]
        public sealed class BooleanEntry
        {
            public string key = "";
            public bool value;
        }

        [Serializable]
        public sealed class TextEntry
        {
            public string key = "";
            public string value = "";
        }

        [Serializable]
        public sealed class NumericEntry
        {
            public string key = "";
            public double value;
        }

        [SerializeField] private BooleanEntry[] booleans = Array.Empty<BooleanEntry>();
        [SerializeField] private TextEntry[] text = Array.Empty<TextEntry>();
        [SerializeField] private NumericEntry[] numeric = Array.Empty<NumericEntry>();

        private readonly Dictionary<string, bool> runtimeBooleans =
            new Dictionary<string, bool>(StringComparer.Ordinal);
        private readonly Dictionary<string, string> runtimeText =
            new Dictionary<string, string>(StringComparer.Ordinal);
        private readonly Dictionary<string, double> runtimeNumeric =
            new Dictionary<string, double>(StringComparer.Ordinal);

        public void SetBoolean(string key, bool value) => runtimeBooleans[Normalize(key)] = value;
        public void SetText(string key, string value) => runtimeText[Normalize(key)] = value ?? "";

        public void SetNumeric(string key, double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value))
                throw new ArgumentOutOfRangeException(nameof(value), "observable state must be finite");
            runtimeNumeric[Normalize(key)] = value;
        }

        public IReadOnlyDictionary<string, bool> SnapshotBooleans()
        {
            var result = new Dictionary<string, bool>(StringComparer.Ordinal);
            foreach (var item in booleans ?? Array.Empty<BooleanEntry>())
                if (item != null && !String.IsNullOrWhiteSpace(item.key)) result[Normalize(item.key)] = item.value;
            foreach (var item in runtimeBooleans) result[item.Key] = item.Value;
            return result;
        }

        public IReadOnlyDictionary<string, string> SnapshotText()
        {
            var result = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var item in text ?? Array.Empty<TextEntry>())
                if (item != null && !String.IsNullOrWhiteSpace(item.key)) result[Normalize(item.key)] = item.value ?? "";
            foreach (var item in runtimeText) result[item.Key] = item.Value;
            return result;
        }

        public IReadOnlyDictionary<string, double> SnapshotNumeric()
        {
            var result = new Dictionary<string, double>(StringComparer.Ordinal);
            foreach (var item in numeric ?? Array.Empty<NumericEntry>())
                if (item != null && !String.IsNullOrWhiteSpace(item.key) &&
                    !double.IsNaN(item.value) && !double.IsInfinity(item.value))
                    result[Normalize(item.key)] = item.value;
            foreach (var item in runtimeNumeric) result[item.Key] = item.Value;
            return result;
        }

        private static string Normalize(string key)
        {
            if (String.IsNullOrWhiteSpace(key))
                throw new ArgumentException("observable state key is required", nameof(key));
            var normalized = key.Trim().ToLowerInvariant();
            if (normalized == "present" || normalized == "role" || normalized == "stableid" ||
                normalized == "x" || normalized == "y" || normalized == "z")
                throw new ArgumentException("observable state key is reserved", nameof(key));
            return normalized;
        }
    }
}
