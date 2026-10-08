using System;
using System.Collections.Generic;

namespace GameBenchmark
{
    /// <summary>Candidate-declared numeric telemetry; never evaluator ground truth by itself.</summary>
    public static class GBTelemetry
    {
        private static readonly Dictionary<string, double> Numeric = new Dictionary<string, double>();

        public static event Action<string, double> NumericReported;

        public static void ReportNumeric(string slot, double value)
        {
            if (string.IsNullOrWhiteSpace(slot))
                throw new ArgumentException("numeric slot is required", nameof(slot));
            if (double.IsNaN(value) || double.IsInfinity(value))
                throw new ArgumentOutOfRangeException(nameof(value), "numeric telemetry must be finite");
            string normalized = slot.Trim().ToLowerInvariant();
            Numeric[normalized] = value;
            NumericReported?.Invoke(normalized, value);
        }

        public static bool TryGetNumeric(string slot, out double value)
        {
            return Numeric.TryGetValue((slot ?? "").Trim().ToLowerInvariant(), out value);
        }

        public static IReadOnlyDictionary<string, double> SnapshotNumeric()
        {
            return new Dictionary<string, double>(Numeric);
        }

        public static void Clear()
        {
            Numeric.Clear();
        }
    }
}
