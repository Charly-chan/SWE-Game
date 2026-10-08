using System;

namespace GameBenchmark
{
    /// <summary>Candidate outcome observations. Python remains the pass/fail authority.</summary>
    public static class GBOutcome
    {
        public readonly struct Event
        {
            public Event(string kind, string value)
            {
                Kind = kind;
                Value = value;
            }

            public string Kind { get; }
            public string Value { get; }
        }

        public static event Action<Event> Reported;

        public static void ReportSuccess()
        {
            Reported?.Invoke(new Event("success", ""));
        }

        public static void ReportFailure(string reason = "")
        {
            Reported?.Invoke(new Event("failure", reason ?? ""));
        }

        public static void ReportCheckpoint(string id)
        {
            if (string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("checkpoint id is required", nameof(id));
            Reported?.Invoke(new Event("checkpoint", id.Trim()));
        }
    }
}
