// Evaluator-owned observer for GameBenchmark Unity Mode 5.
// The player receives public run metadata and one current action at a time.
// Hidden goals, policies, expected outcomes, and verdicts stay in Python.

using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using GameBenchmark;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Controls;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.SceneManagement;

[Serializable]
internal sealed class GBControllerAction
{
    public string canonical_action;
    public float value;
    public string[] actions;
    public string[] axis_ids;
    public float[] axis_values;
    public int hold_frames;
}

[Serializable]
internal sealed class GBControllerCommand
{
    public string type;
    public int sequence;
    public string canonical_action;
    public float value;
    public string[] actions;
    public string[] axis_ids;
    public float[] axis_values;
    public int hold_frames;
    public string checkpoint_id;
    public string reason;
    public GBControllerAction[] batch;
}

public sealed class GameBenchmarkEvaluatorProbe : MonoBehaviour
{
    private const string Protocol = "gamebench.unity-controller.v1";
    private const int MaxMessageBytes = 1024 * 1024;
    private const int SettleFrames = 6;
    private const int PlayerDiscoveryFrames = 300;

    private readonly ConcurrentQueue<GBControllerCommand> commands =
        new ConcurrentQueue<GBControllerCommand>();
    private readonly ConcurrentQueue<string> readerFailures =
        new ConcurrentQueue<string>();
    private readonly object sendLock = new object();
    private readonly object eventLock = new object();
    private readonly List<string> pendingEvents = new List<string>();
    private readonly Dictionary<string, double> numeric =
        new Dictionary<string, double>(StringComparer.Ordinal);
    private readonly HashSet<string> requiredRoles = new HashSet<string>(StringComparer.Ordinal);
    private readonly HashSet<string> declaredNumeric = new HashSet<string>(StringComparer.Ordinal);
    private readonly HashSet<string> supportedActions = new HashSet<string>(StringComparer.Ordinal);
    private readonly HashSet<string> supportedAxes = new HashSet<string>(StringComparer.Ordinal);
    private readonly HashSet<string> knownEntities = new HashSet<string>(StringComparer.Ordinal);
    private readonly HashSet<string> scenesVisited = new HashSet<string>(StringComparer.Ordinal);
    private readonly Dictionary<string, string> deviceIdByEntity =
        new Dictionary<string, string>(StringComparer.Ordinal);
    private readonly Dictionary<string, int> nextDeviceOrdinalByKind =
        new Dictionary<string, int>(StringComparer.Ordinal);
    private readonly Dictionary<string, string[]> knownDeviceIdentity =
        new Dictionary<string, string[]>(StringComparer.Ordinal);
    private readonly Dictionary<string, List<ButtonControl>> actionControls =
        new Dictionary<string, List<ButtonControl>>(StringComparer.Ordinal);
    private readonly Dictionary<string, List<AxisControl>> axisControls =
        new Dictionary<string, List<AxisControl>>(StringComparer.Ordinal);

    private TcpClient client;
    private NetworkStream stream;
    private Thread reader;
    private Keyboard keyboard;
    private string runId = "";
    private string nonce = "";
    private string buildDigest = "";
    private string startScene = "";
    private int declaredLevelCount;
    private int physicsFrame;
    private int observationSequence;
    private int lastObservationFrame = -1;
    private int observationStride = 1;
    private bool observationDirty = true;
    private int lastCommandSequence;
    private bool ready;
    private bool busy;
    private bool stopping;
    private bool wholeGameClear;
    private bool boundsObservable;
    private float evaluatorTimeScale = 1.0f;
    private Bounds frozenBounds;
    private Vector3 lastPlayerPosition;
    private bool hasLastPlayerPosition;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
    private static void Bootstrap()
    {
        var controllerPort = ReadArgument("--gb-controller-port=");
        if (String.IsNullOrEmpty(controllerPort))
        {
            Debug.LogError("GameBenchmark observer bootstrap has no controller port argument");
            return;
        }
        Debug.Log("GameBenchmark observer bootstrap starting");
        var host = new GameObject("__GameBenchmarkEvaluatorObserver");
        DontDestroyOnLoad(host);
        host.AddComponent<GameBenchmarkEvaluatorProbe>();
    }

    private IEnumerator Start()
    {
        // Certified Linux runs use Xvfb without a window manager. Unity pauses
        // the Player loop when that display never grants focus unless this is
        // set explicitly; the controller can otherwise receive `hello` while
        // the coroutine and FixedUpdate remain suspended forever.
        Application.runInBackground = true;
        QualitySettings.vSyncCount = 0;
        ReadArguments();
        ApplyEvaluatorClock();
        Application.targetFrameRate = evaluatorTimeScale > 1.0f ? -1 : 60;
        Application.logMessageReceived += OnLog;
        SceneManager.sceneLoaded += OnSceneLoaded;
        GBOutcome.Reported += OnOutcome;
        GBTelemetry.NumericReported += OnNumeric;
        foreach (var pair in GBTelemetry.SnapshotNumeric()) OnNumeric(pair.Key, pair.Value);
        // Development players run in the background.  The Input System default
        // focus policy disables keyboard devices there, which would discard the
        // evaluator's queued state before gameplay observes it.  Keep the
        // evaluator-owned virtual device active and process its events only at
        // the explicit dispatch points below.
        InputSystem.settings.backgroundBehavior = InputSettings.BackgroundBehavior.IgnoreFocus;
        InputSystem.settings.updateMode = InputSettings.UpdateMode.ProcessEventsManually;
        // The virtual device must exist before PlayerInput resolves its fixed
        // Keyboard control scheme in a headless player.
        keyboard = Keyboard.current ?? InputSystem.AddDevice<Keyboard>();

        try
        {
            Debug.Log("GameBenchmark observer connecting to loopback controller");
            client = new TcpClient(AddressFamily.InterNetwork) { NoDelay = true };
            client.Connect(IPAddress.Loopback, ReadIntArgument("--gb-controller-port="));
            stream = client.GetStream();
            Send("{\"type\":\"hello\",\"protocol\":\"" + Protocol +
                 "\",\"run_id\":" + Quote(runId) + ",\"nonce\":" + Quote(nonce) +
                 ",\"build_digest\":" + Quote(buildDigest) + "}");
            reader = new Thread(ReadLoop)
            {
                IsBackground = true,
                Name = "GameBenchmarkObserverReader",
            };
            reader.Start();
        }
        catch (Exception exception)
        {
            Debug.LogError("GameBenchmark observer could not connect: " + exception.Message);
            Application.Quit(2);
            yield break;
        }

        if (!String.IsNullOrEmpty(startScene) && !SceneMatches(SceneManager.GetActiveScene(), startScene))
        {
            var load = SceneManager.LoadSceneAsync(startScene, LoadSceneMode.Single);
            if (load == null)
            {
                Fatal("start scene could not be loaded: " + startScene);
                yield break;
            }
            while (!load.isDone) yield return null;
        }
        RecordLoadedScenes();
        for (var index = 0; index < SettleFrames; index++) yield return new WaitForFixedUpdate();
        for (var index = 0; index < PlayerDiscoveryFrames && !HasActivePlayer(); index++)
            yield return new WaitForFixedUpdate();
        if (!HasActivePlayer())
        {
            Fatal("required gb_player marker was not observable after settle");
            yield break;
        }

        foreach (var playerInput in Resources.FindObjectsOfTypeAll<PlayerInput>().Where(item =>
                     item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded))
        {
            playerInput.actions?.Enable();
            if (playerInput.actions != null && playerInput.actions.controlSchemes.Any(item => item.name == "Keyboard"))
                playerInput.SwitchCurrentControlScheme("Keyboard", keyboard);
        }
        ResolveInputControls();
        FreezeBounds();
        Send("{\"type\":\"ready\",\"scene\":" + Quote(SceneManager.GetActiveScene().path) +
             ",\"frame\":" + physicsFrame + "}");
        ready = true;
        QueueBoundsEvent();
        SendObservation();
    }

    private void Update()
    {
        if (stopping) return;
        // Candidate scripts are allowed to set their own gameplay defaults in
        // Start/Update.  Re-assert the evaluator-owned clock every frame so a
        // candidate cannot silently turn an accelerated run back into realtime.
        ApplyEvaluatorClock();
        if (readerFailures.TryDequeue(out var readerFailure))
        {
            Fatal(readerFailure);
            return;
        }
        while (!busy && commands.TryDequeue(out var command))
        {
            if (command.sequence <= lastCommandSequence && command.type != "stop")
            {
                Fatal("controller command sequence is duplicate or out of order");
                return;
            }
            if (command.type != "stop") lastCommandSequence = command.sequence;
            if (command.type == "action")
            {
                busy = true;
                StartCoroutine(HandleAction(command));
            }
            else if (command.type == "action_batch")
            {
                busy = true;
                StartCoroutine(HandleActionBatch(command));
            }
            else if (command.type == "capture")
            {
                busy = true;
                StartCoroutine(HandleCapture(command));
            }
            else if (command.type == "stop")
            {
                stopping = true;
                ReleaseInput();
                Send("{\"type\":\"stop_ack\",\"reason\":" + Quote(command.reason) + "}");
                Application.Quit(0);
            }
            else Fatal("unsupported controller command: " + command.type);
        }
    }

    private void FixedUpdate()
    {
        ApplyEvaluatorClock();
        physicsFrame++;
        if (ready && !stopping && ShouldSendObservation()) SendObservation();
    }

    private void LateUpdate()
    {
        // LateUpdate runs after ordinary candidate Update methods.  This is the
        // final authority for the frame and closes the common `Time.timeScale =
        // 1` reset pattern used by game bootstrap scripts.
        ApplyEvaluatorClock();
    }

    private IEnumerator HandleAction(GBControllerCommand command)
    {
        var actions = command.actions != null && command.actions.Length > 0
            ? command.actions : (String.IsNullOrEmpty(command.canonical_action)
                ? new string[0] : new[] { command.canonical_action });
        var axisIds = command.axis_ids ?? new string[0];
        var axisValues = command.axis_values ?? new float[0];
        if (command.hold_frames <= 0 || actions.Any(item => !supportedActions.Contains(item)) ||
            axisIds.Length != axisValues.Length || axisIds.Any(item => !supportedAxes.Contains(item)) ||
            axisValues.Any(item => Single.IsNaN(item) || Single.IsInfinity(item)))
        {
            Fatal("invalid or undeclared action/axis state");
            yield break;
        }
        var startFrame = physicsFrame;
        try
        {
            SetInput(actions, axisIds, axisValues);
        }
        catch (Exception exception)
        {
            busy = false;
            Fatal("input dispatch failed: " + exception.Message);
            yield break;
        }
        for (var index = 0; index < command.hold_frames; index++)
            yield return new WaitForFixedUpdate();
        ReleaseInput();
        SendObservation();
        Send("{\"type\":\"action_ack\",\"sequence\":" + command.sequence +
             ",\"start_frame\":" + startFrame + ",\"end_frame\":" + physicsFrame + "}");
        busy = false;
    }

    private IEnumerator HandleActionBatch(GBControllerCommand command)
    {
        if (command.batch == null || command.batch.Length == 0 || command.batch.Length > 64)
        {
            busy = false;
            Fatal("invalid action batch size");
            yield break;
        }
        var startFrame = physicsFrame;
        foreach (var step in command.batch)
        {
            var actions = step.actions != null && step.actions.Length > 0
                ? step.actions : (String.IsNullOrEmpty(step.canonical_action)
                    ? new string[0] : new[] { step.canonical_action });
            var axisIds = step.axis_ids ?? new string[0];
            var axisValues = step.axis_values ?? new float[0];
            if (step.hold_frames <= 0 || step.hold_frames > 600 ||
                actions.Any(item => !supportedActions.Contains(item)) ||
                axisIds.Length != axisValues.Length ||
                axisIds.Any(item => !supportedAxes.Contains(item)) ||
                axisValues.Any(item => Single.IsNaN(item) || Single.IsInfinity(item)))
            {
                ReleaseInput();
                busy = false;
                Fatal("invalid action in batch");
                yield break;
            }
            try
            {
                SetInput(actions, axisIds, axisValues);
            }
            catch (Exception exception)
            {
                ReleaseInput();
                busy = false;
                Fatal("batched input dispatch failed: " + exception.Message);
                yield break;
            }
            for (var index = 0; index < step.hold_frames; index++)
                yield return new WaitForFixedUpdate();
            ReleaseInput();
        }
        SendObservation();
        Send("{\"type\":\"action_batch_ack\",\"sequence\":" + command.sequence +
             ",\"start_frame\":" + startFrame + ",\"end_frame\":" + physicsFrame +
             ",\"action_count\":" + command.batch.Length + "}");
        busy = false;
    }

    private IEnumerator HandleCapture(GBControllerCommand command)
    {
        yield return new WaitForEndOfFrame();
        Texture2D texture = null;
        try
        {
            texture = new Texture2D(Screen.width, Screen.height, TextureFormat.RGB24, false);
            texture.ReadPixels(new Rect(0, 0, Screen.width, Screen.height), 0, 0);
            texture.Apply(false, false);
            var png = texture.EncodeToPNG();
            using (var sha = SHA256.Create())
            {
                var digest = Hex(sha.ComputeHash(png));
                Send("{\"type\":\"capture_ack\",\"sequence\":" + command.sequence +
                     ",\"frame\":" + physicsFrame + ",\"checkpoint_id\":" +
                     Quote(command.checkpoint_id ?? "") + ",\"bytes_digest\":\"sha256:" + digest +
                     "\",\"png_base64\":" + Quote(Convert.ToBase64String(png)) + "}");
            }
        }
        catch (Exception exception) { Fatal("capture failed: " + exception.Message); }
        finally
        {
            if (texture != null) Destroy(texture);
            busy = false;
        }
    }

    private void SendObservation()
    {
        try
        {
            // Action/capture coroutines may finish on the same fixed frame as
            // FixedUpdate. Never emit a duplicate frame: the controller's
            // semantic stream requires strictly increasing frame numbers.
            if (physicsFrame <= lastObservationFrame) return;
            RecordLoadedScenes();
            DiscoverDeviceIdentities();
            var entities = ActiveEntities();
            RecordEntityLifecycle(entities);
            var groups = GroupCensus(entities);
            var overlaps = Overlaps(entities);
            var deviceObservation = ObserveDevices(entities);
            var player = entities.FirstOrDefault(item => item.Role == "gb_player");
            var position = player != null ? player.transform.position : Vector3.zero;
            var velocity = hasLastPlayerPosition
                ? (position - lastPlayerPosition) / Math.Max(Time.fixedDeltaTime, 0.000001f)
                : Vector3.zero;
            lastPlayerPosition = position;
            hasLastPlayerPosition = true;
            var frame = physicsFrame;
            var row = "{\"f\":" + frame + ",\"g\":" + IntMap(groups) +
                ",\"o\":" + BoolMap(overlaps) + ",\"px\":" + Number(position.x) +
                ",\"py\":" + Number(position.y) + ",\"pz\":" + Number(position.z) +
                ",\"n\":" + NumericMap() + ",\"wgc\":" + Bool(wholeGameClear) +
                ",\"lv\":" + scenesVisited.Count + ",\"d\":" + deviceObservation.Item1 +
                ",\"c\":" + deviceObservation.Item2 + ",\"so\":\"\"" +
                ",\"vx\":" + Number(velocity.x) + ",\"vy\":" + Number(velocity.y) +
                ",\"vz\":" + Number(velocity.z) + ",\"s\":{\"numeric\":" + NumericMap() +
                ",\"audio_events\":0,\"anim\":" + AnimationCount() +
                ",\"node_count\":" + NodeCount() + ",\"visible_count\":" + VisibleCount() +
                ",\"text\":" + VisibleTextCount() + "}}";
            observationSequence++;
            lastObservationFrame = physicsFrame;
            observationDirty = false;
            Send("{\"type\":\"observation\",\"sequence\":" + observationSequence +
                 ",\"row\":" + row + ",\"events\":" + DrainEvents() + "}");
        }
        catch (Exception exception) { Fatal("observation failed: " + exception.Message); }
    }

    private bool ShouldSendObservation()
    {
        if (observationDirty || lastObservationFrame < 0) return true;
        return physicsFrame - lastObservationFrame >= observationStride;
    }

    private static List<GBEntity> ActiveEntities()
    {
        return Resources.FindObjectsOfTypeAll<GBEntity>()
            .Where(item => item != null && item.gameObject.scene.IsValid() &&
                           item.gameObject.scene.isLoaded && item.gameObject.activeInHierarchy &&
                           !String.IsNullOrWhiteSpace(item.Role))
            .OrderBy(item => item.Role, StringComparer.Ordinal)
            .ThenBy(item => String.IsNullOrEmpty(item.StableId) ? item.GetInstanceID().ToString() : item.StableId,
                    StringComparer.Ordinal)
            .ToList();
    }

    private bool HasActivePlayer() => ActiveEntities().Any(item => item.Role == "gb_player");

    private void DiscoverDeviceIdentities()
    {
        foreach (var entity in Resources.FindObjectsOfTypeAll<GBEntity>()
            .Where(item => item != null && item.gameObject.scene.IsValid() &&
                           item.gameObject.scene.isLoaded && !String.IsNullOrWhiteSpace(item.Role))
            .OrderBy(item => item.Role, StringComparer.Ordinal)
            .ThenBy(item => String.IsNullOrEmpty(item.StableId)
                ? item.GetInstanceID().ToString(CultureInfo.InvariantCulture)
                : item.StableId, StringComparer.Ordinal))
            DeviceKey(entity);
    }

    private string DeviceKey(GBEntity entity)
    {
        var role = entity.Role ?? "";
        var kind = role.StartsWith("gb_", StringComparison.Ordinal) ? role.Substring(3) : role;
        var stable = String.IsNullOrEmpty(entity.StableId)
            ? entity.GetInstanceID().ToString(CultureInfo.InvariantCulture)
            : entity.StableId;
        var identity = role + "|" + stable;
        if (deviceIdByEntity.TryGetValue(identity, out var existing)) return existing;
        var ordinal = nextDeviceOrdinalByKind.TryGetValue(kind, out var previous)
            ? previous : 0;
        nextDeviceOrdinalByKind[kind] = ordinal + 1;
        var key = kind + "#" + ordinal;
        deviceIdByEntity[identity] = key;
        knownDeviceIdentity[key] = new[] { role, stable };
        return key;
    }

    // Stable device keys are derived from evaluator-owned semantic roles and
    // deterministic role ordinals. Never infer them from candidate object names
    // or arbitrary Unity enumeration order. GBEntity is a semantic marker and
    // candidates commonly put their sprite or collider below that marker.
    // Resolve the complete marker subtree using the public observation contract.
    private Tuple<string, string> ObserveDevices(IReadOnlyList<GBEntity> entities)
    {
        var players = entities.Where(item => item.Role == "gb_player").ToArray();
        var devices = new StringBuilder("{");
        var contacts = new List<string>();
        var activeKeys = new HashSet<string>(StringComparer.Ordinal);
        var first = true;
        foreach (var entity in entities)
        {
            var role = entity.Role ?? "";
            var key = DeviceKey(entity);
            activeKeys.Add(key);
            if (!first) devices.Append(",");
            first = false;
            devices.Append(Quote(key));
            devices.Append(":{\"present\":true,\"role\":");
            devices.Append(Quote(role));
            devices.Append(",\"stableId\":");
            devices.Append(Quote(entity.StableId ?? ""));
            var position = entity.transform.position;
            devices.Append(",\"x\":");
            devices.Append(Number(position.x));
            devices.Append(",\"y\":");
            devices.Append(Number(position.y));
            devices.Append(",\"z\":");
            devices.Append(Number(position.z));
            AppendObservableState(devices, entity);
            devices.Append("}");

            if (players.Length != 1 || entity.Role == "gb_player") continue;
            if (!TryBounds(entity.gameObject, out var entityBounds)) continue;
            if (!TryBounds(players[0].gameObject, out var playerBounds)) continue;
            if (ResolvedContact(entity, players[0], entityBounds, playerBounds))
                contacts.Add(key);
        }
        foreach (var pair in knownDeviceIdentity.OrderBy(item => item.Key, StringComparer.Ordinal))
        {
            if (activeKeys.Contains(pair.Key)) continue;
            if (!first) devices.Append(",");
            first = false;
            devices.Append(Quote(pair.Key));
            devices.Append(":{\"present\":false,\"role\":");
            devices.Append(Quote(pair.Value[0]));
            devices.Append(",\"stableId\":");
            devices.Append(Quote(pair.Value[1]));
            devices.Append("}");
        }
        devices.Append("}");
        return Tuple.Create(devices.ToString(), "[" + String.Join(",", contacts.Select(Quote)) + "]");
    }

    private static void AppendObservableState(StringBuilder target, GBEntity entity)
    {
        var observable = entity.GetComponentInChildren<GBObservableState>(true);
        if (observable == null) return;
        var reserved = new HashSet<string>(
            new[] { "present", "role", "stableid", "x", "y", "z" },
            StringComparer.Ordinal);
        foreach (var item in observable.SnapshotBooleans().OrderBy(item => item.Key, StringComparer.Ordinal))
        {
            if (reserved.Contains(item.Key)) continue;
            target.Append(",");
            target.Append(Quote(item.Key));
            target.Append(":");
            target.Append(Bool(item.Value));
        }
        foreach (var item in observable.SnapshotText().OrderBy(item => item.Key, StringComparer.Ordinal))
        {
            if (reserved.Contains(item.Key)) continue;
            target.Append(",");
            target.Append(Quote(item.Key));
            target.Append(":");
            target.Append(Quote(item.Value));
        }
        foreach (var item in observable.SnapshotNumeric().OrderBy(item => item.Key, StringComparer.Ordinal))
        {
            if (reserved.Contains(item.Key)) continue;
            target.Append(",");
            target.Append(Quote(item.Key));
            target.Append(":");
            target.Append(Number(item.Value));
        }
    }

    private static bool ResolvedContact(
        GBEntity left,
        GBEntity right,
        Bounds leftBounds,
        Bounds rightBounds)
    {
        var left2 = left.GetComponentsInChildren<Collider2D>(true)
            .Where(item => item != null && item.enabled && item.gameObject.activeInHierarchy)
            .ToArray();
        var right2 = right.GetComponentsInChildren<Collider2D>(true)
            .Where(item => item != null && item.enabled && item.gameObject.activeInHierarchy)
            .ToArray();
        if (left2.Length > 0 && right2.Length > 0)
        {
            foreach (var a in left2)
                foreach (var b in right2)
                {
                    var distance = Physics2D.Distance(a, b);
                    if (distance.isValid && (distance.isOverlapped || distance.distance <= 0.0f))
                        return true;
                }
            return false;
        }

        var left3 = left.GetComponentsInChildren<Collider>(true)
            .Where(item => item != null && item.enabled && item.gameObject.activeInHierarchy)
            .ToArray();
        var right3 = right.GetComponentsInChildren<Collider>(true)
            .Where(item => item != null && item.enabled && item.gameObject.activeInHierarchy)
            .ToArray();
        if (left3.Length > 0 && right3.Length > 0)
        {
            foreach (var a in left3)
                foreach (var b in right3)
                {
                    if (Physics.ComputePenetration(
                        a, a.transform.position, a.transform.rotation,
                        b, b.transform.position, b.transform.rotation,
                        out var direction, out var distance))
                        return true;
                }
            return false;
        }

        // A custom-geometry game may intentionally use its own collision model.
        // The public contract therefore defines aggregate rendered bounds as a
        // deterministic fallback, never as a candidate-authored boolean.
        return leftBounds.Intersects(rightBounds);
    }

    private Dictionary<string, int> GroupCensus(IEnumerable<GBEntity> entities)
    {
        var groups = requiredRoles.ToDictionary(role => role, _ => 0, StringComparer.Ordinal);
        foreach (var entity in entities)
        {
            if (!groups.ContainsKey(entity.Role)) groups[entity.Role] = 0;
            groups[entity.Role]++;
        }
        return groups;
    }

    private static Dictionary<string, bool> Overlaps(IReadOnlyList<GBEntity> entities)
    {
        var result = new Dictionary<string, bool>(StringComparer.Ordinal);
        for (var left = 0; left < entities.Count; left++)
        {
            if (!TryBounds(entities[left].gameObject, out var leftBounds)) continue;
            for (var right = left + 1; right < entities.Count; right++)
            {
                if (!TryBounds(entities[right].gameObject, out var rightBounds)) continue;
                var a = entities[left].Role;
                var b = entities[right].Role;
                var hit = leftBounds.Intersects(rightBounds);
                if (a == "gb_player" || b == "gb_player")
                {
                    var other = a == "gb_player" ? b : a;
                    var player = a == "gb_player" ? entities[left] : entities[right];
                    var target = a == "gb_player" ? entities[right] : entities[left];
                    var playerBounds = a == "gb_player" ? leftBounds : rightBounds;
                    var targetBounds = a == "gb_player" ? rightBounds : leftBounds;
                    hit = ResolvedContact(player, target, playerBounds, targetBounds);
                    result[other] = result.TryGetValue(other, out var previous) ? previous || hit : hit;
                }
                else
                {
                    var key = String.CompareOrdinal(a, b) <= 0 ? a + "|" + b : b + "|" + a;
                    result[key] = result.TryGetValue(key, out var previous) ? previous || hit : hit;
                }
            }
        }
        return result;
    }

    private static bool TryBounds(GameObject gameObject, out Bounds bounds)
    {
        var foundCollider = false;
        var aggregate = default(Bounds);
        foreach (var collider in gameObject.GetComponentsInChildren<Collider>(true))
        {
            if (collider == null || !collider.enabled || !collider.gameObject.activeInHierarchy) continue;
            AddBounds(collider.bounds, ref aggregate, ref foundCollider);
        }
        foreach (var collider in gameObject.GetComponentsInChildren<Collider2D>(true))
        {
            if (collider == null || !collider.enabled || !collider.gameObject.activeInHierarchy) continue;
            AddBounds(collider.bounds, ref aggregate, ref foundCollider);
        }
        if (foundCollider)
        {
            bounds = aggregate;
            return true;
        }

        var foundRenderer = false;
        aggregate = default(Bounds);
        foreach (var renderer in gameObject.GetComponentsInChildren<Renderer>(true))
        {
            if (renderer == null || !renderer.enabled || !renderer.gameObject.activeInHierarchy) continue;
            AddBounds(renderer.bounds, ref aggregate, ref foundRenderer);
        }
        bounds = aggregate;
        return foundRenderer;
    }

    private static void AddBounds(Bounds candidate, ref Bounds aggregate, ref bool found)
    {
        if (!found)
        {
            aggregate = candidate;
            found = true;
        }
        else aggregate.Encapsulate(candidate);
    }

    private void FreezeBounds()
    {
        boundsObservable = false;
        foreach (var entity in ActiveEntities())
        {
            if (!TryBounds(entity.gameObject, out var bounds)) continue;
            if (!boundsObservable) { frozenBounds = bounds; boundsObservable = true; }
            else frozenBounds.Encapsulate(bounds);
        }
    }

    private void QueueBoundsEvent()
    {
        var min = boundsObservable ? frozenBounds.min : Vector3.zero;
        var max = boundsObservable ? frozenBounds.max : Vector3.zero;
        AddEvent("{\"kind\":\"bounds_frozen\",\"observable\":" + Bool(boundsObservable) +
                 ",\"min\":" + Vector(min) + ",\"max\":" + Vector(max) + "}");
    }

    private void RecordEntityLifecycle(IEnumerable<GBEntity> entities)
    {
        var current = new HashSet<string>(StringComparer.Ordinal);
        foreach (var entity in entities)
        {
            var stable = String.IsNullOrEmpty(entity.StableId)
                ? entity.GetInstanceID().ToString(CultureInfo.InvariantCulture) : entity.StableId;
            var key = entity.Role + "|" + stable;
            current.Add(key);
            if (!knownEntities.Contains(key))
            {
                observationDirty = true;
                AddEvent("{\"kind\":\"entity_appeared\",\"role\":" + Quote(entity.Role) +
                         ",\"stable_id\":" + Quote(stable) + ",\"frame\":" + physicsFrame + "}");
            }
        }
        foreach (var key in knownEntities.Where(item => !current.Contains(item)).ToArray())
        {
            observationDirty = true;
            var separator = key.IndexOf('|');
            AddEvent("{\"kind\":\"entity_disappeared\",\"role\":" + Quote(key.Substring(0, separator)) +
                     ",\"stable_id\":" + Quote(key.Substring(separator + 1)) +
                     ",\"frame\":" + physicsFrame + "}");
        }
        knownEntities.Clear();
        knownEntities.UnionWith(current);
    }

    private void OnOutcome(GBOutcome.Event outcome)
    {
        observationDirty = true;
        if (outcome.Kind == "success") wholeGameClear = true;
        var kind = outcome.Kind == "success" ? "outcome_success" :
                   outcome.Kind == "failure" ? "outcome_failure" : "checkpoint";
        AddEvent("{\"kind\":" + Quote(kind) + ",\"value\":" + Quote(outcome.Value ?? "") +
                 ",\"frame\":" + physicsFrame + "}");
    }

    private void OnNumeric(string slot, double value)
    {
        var normalized = (slot ?? "").Trim().ToLowerInvariant();
        if (declaredNumeric.Contains(normalized))
        {
            if (!numeric.TryGetValue(normalized, out var previous) || Math.Abs(previous - value) > 0.000001)
                observationDirty = true;
            numeric[normalized] = value;
        }
    }

    private void OnLog(string condition, string stackTrace, LogType type)
    {
        if (type != LogType.Error && type != LogType.Exception && type != LogType.Assert) return;
        AddEvent("{\"kind\":\"error\",\"message\":" + Quote(condition ?? "Unity error") +
                 ",\"frame\":" + physicsFrame + "}");
    }

    private void OnSceneLoaded(Scene scene, LoadSceneMode mode)
    {
        observationDirty = true;
        ApplyEvaluatorClock();
        RecordLoadedScenes();
        AddEvent("{\"kind\":\"scene_loaded\",\"scene\":" + Quote(scene.path) +
                 ",\"frame\":" + physicsFrame + "}");
    }

    private void ApplyEvaluatorClock()
    {
        if (evaluatorTimeScale < 1.0f) evaluatorTimeScale = 1.0f;
        Time.timeScale = evaluatorTimeScale;
        // A larger timeScale should advance fixed-update simulation faster,
        // while preserving the candidate's nominal physics step.
        Application.targetFrameRate = evaluatorTimeScale > 1.0f ? -1 : 60;
    }

    private void RecordLoadedScenes()
    {
        for (var index = 0; index < SceneManager.sceneCount; index++)
        {
            var scene = SceneManager.GetSceneAt(index);
            if (scene.IsValid() && scene.isLoaded) scenesVisited.Add(scene.path);
        }
    }

    private void SetInput(string[] actions, string[] axisIds, float[] axisValues)
    {
        QueueResolvedControlState(
            new HashSet<string>(actions, StringComparer.Ordinal),
            axisIds.Select((id, index) => new { id, value = axisValues[index] })
                .ToDictionary(item => item.id, item => item.value, StringComparer.Ordinal));
    }

    private void ReleaseInput()
    {
        QueueResolvedControlState(
            new HashSet<string>(StringComparer.Ordinal),
            new Dictionary<string, float>(StringComparer.Ordinal));
    }

    private void ResolveInputControls()
    {
        actionControls.Clear();
        axisControls.Clear();
        foreach (var actionName in supportedActions) actionControls[actionName] = new List<ButtonControl>();
        foreach (var axisName in supportedAxes) axisControls[axisName] = new List<AxisControl>();
        foreach (var playerInput in Resources.FindObjectsOfTypeAll<PlayerInput>().Where(item =>
                     item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded &&
                     item.actions != null))
        {
            foreach (var actionName in supportedActions)
            {
                var action = playerInput.actions.FindAction(actionName, false);
                if (action == null) continue;
                action.Enable();
                foreach (var control in action.controls.OfType<ButtonControl>())
                    if (!actionControls[actionName].Contains(control)) actionControls[actionName].Add(control);
            }
            foreach (var axisName in supportedAxes)
            {
                var action = playerInput.actions.FindAction(axisName, false);
                if (action == null) continue;
                action.Enable();
                foreach (var control in action.controls.OfType<AxisControl>())
                    if (!axisControls[axisName].Contains(control)) axisControls[axisName].Add(control);
            }
        }
        // PlayerInput is a convenience component, not part of the public
        // contract. Candidate code may own and enable an InputActionAsset
        // directly (or construct named actions in code), so resolve every
        // globally enabled canonical action as well.
        foreach (var action in InputSystem.ListEnabledActions())
        {
            if (action == null || String.IsNullOrEmpty(action.name)) continue;
            if (actionControls.TryGetValue(action.name, out var buttons))
                foreach (var control in action.controls.OfType<ButtonControl>())
                    if (!buttons.Contains(control)) buttons.Add(control);
            if (axisControls.TryGetValue(action.name, out var axes))
                foreach (var control in action.controls.OfType<AxisControl>())
                    if (!axes.Contains(control)) axes.Add(control);
        }
    }

    private void QueueResolvedControlState(
        HashSet<string> activeActions, Dictionary<string, float> activeAxes)
    {
        foreach (var activeAction in activeActions)
            if (!actionControls.TryGetValue(activeAction, out var resolved) || resolved.Count == 0)
                throw new InvalidOperationException("action has no enabled ButtonControl: " + activeAction);
        foreach (var axis in activeAxes.Keys)
            if (!axisControls.TryGetValue(axis, out var resolved) || resolved.Count == 0)
                throw new InvalidOperationException("axis has no enabled AxisControl: " + axis);

        var perDevice = new Dictionary<InputDevice, Dictionary<InputControl<float>, float>>();
        foreach (var pair in actionControls)
        {
            var value = activeActions.Contains(pair.Key) ? 1.0f : 0.0f;
            foreach (var control in pair.Value)
            {
                if (!perDevice.TryGetValue(control.device, out var values))
                {
                    values = new Dictionary<InputControl<float>, float>();
                    perDevice[control.device] = values;
                }
                values[control] = value;
            }
        }
        foreach (var pair in axisControls)
        {
            var value = activeAxes.TryGetValue(pair.Key, out var declared) ? declared : 0.0f;
            foreach (var control in pair.Value)
            {
                if (!perDevice.TryGetValue(control.device, out var values))
                {
                    values = new Dictionary<InputControl<float>, float>();
                    perDevice[control.device] = values;
                }
                values[control] = value;
            }
        }
        foreach (var entry in perDevice)
        {
            // A development player can lose focus before this component's
            // Start method runs. Switching the background policy does not
            // retroactively re-enable a device disabled by the old policy.
            if (!entry.Key.enabled) InputSystem.EnableDevice(entry.Key);
            InputEventPtr eventPointer;
            using (StateEvent.From(entry.Key, out eventPointer))
            {
                foreach (var control in entry.Value) control.Key.WriteValueIntoEvent(control.Value, eventPointer);
                InputSystem.QueueEvent(eventPointer);
            }
        }
        // Certified headless Linux players have no native keyboard backend to
        // drive an automatic input tick. Consume the queued virtual-device
        // state through the Input System itself so action callbacks observe it.
        InputSystem.Update();
        var resolvedDeviceCount = perDevice.Keys.Count;
        var enabledDeviceCount = perDevice.Keys.Count(device => device.enabled);
        AddEvent("{\"kind\":\"input_dispatched\",\"actions\":" +
                 StringArray(activeActions.OrderBy(item => item, StringComparer.Ordinal)) +
                 ",\"axes\":" + FloatMap(activeAxes) +
                 ",\"resolved_device_count\":" + resolvedDeviceCount +
                 ",\"enabled_device_count\":" + enabledDeviceCount +
                 ",\"frame\":" + physicsFrame + "}");
    }

    private int NodeCount()
    {
        var total = 0;
        for (var index = 0; index < SceneManager.sceneCount; index++)
        {
            var scene = SceneManager.GetSceneAt(index);
            if (!scene.IsValid() || !scene.isLoaded) continue;
            foreach (var root in scene.GetRootGameObjects())
                total += root.GetComponentsInChildren<Transform>(true).Length;
        }
        return total;
    }

    private static int VisibleCount() => Resources.FindObjectsOfTypeAll<Renderer>().Count(item =>
        item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded &&
        item.gameObject.activeInHierarchy && item.enabled && item.isVisible);

    private static int VisibleTextCount() => Resources.FindObjectsOfTypeAll<Component>().Count(item =>
        item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded &&
        item.gameObject.activeInHierarchy && item.GetType().Name.IndexOf("Text", StringComparison.OrdinalIgnoreCase) >= 0);

    // Animation is an optional built-in module and is not part of the frozen
    // pilot package lock. Unknown presentation components remain zero rather
    // than widening the evaluator dependency surface.
    private static int AnimationCount() => 0;

    private void ReadLoop()
    {
        try
        {
            while (!stopping)
            {
                var command = JsonUtility.FromJson<GBControllerCommand>(Receive());
                if (command == null || String.IsNullOrEmpty(command.type))
                    throw new InvalidDataException("invalid controller command");
                commands.Enqueue(command);
            }
        }
        catch (Exception exception)
        {
            // Unity logging and Application.Quit are main-thread APIs.  Carry
            // transport failures back to Update instead of invoking them from
            // the socket reader thread.
            if (!stopping) readerFailures.Enqueue("controller channel failed: " + exception.Message);
        }
    }

    private void Send(string json)
    {
        var payload = Encoding.UTF8.GetBytes(json);
        if (payload.Length <= 0 || payload.Length > MaxMessageBytes)
            throw new InvalidDataException("controller message size is invalid");
        var prefix = BitConverter.GetBytes(IPAddress.HostToNetworkOrder(payload.Length));
        lock (sendLock)
        {
            stream.Write(prefix, 0, prefix.Length);
            stream.Write(payload, 0, payload.Length);
            stream.Flush();
        }
    }

    private string Receive()
    {
        var prefix = ReadExact(4);
        var size = IPAddress.NetworkToHostOrder(BitConverter.ToInt32(prefix, 0));
        if (size <= 0 || size > MaxMessageBytes) throw new InvalidDataException("controller message size");
        return Encoding.UTF8.GetString(ReadExact(size));
    }

    private byte[] ReadExact(int size)
    {
        var buffer = new byte[size];
        var offset = 0;
        while (offset < size)
        {
            var count = stream.Read(buffer, offset, size - offset);
            if (count <= 0) throw new EndOfStreamException("controller channel closed");
            offset += count;
        }
        return buffer;
    }

    private void Fatal(string message)
    {
        if (stopping) return;
        stopping = true;
        try { Send("{\"type\":\"fatal\",\"error\":" + Quote(message) + "}"); } catch { }
        Debug.LogError("GameBenchmark observer fatal: " + message);
        Application.Quit(2);
    }

    private void AddEvent(string json)
    {
        lock (eventLock) pendingEvents.Add(json);
    }

    private string DrainEvents()
    {
        lock (eventLock)
        {
            var json = "[" + String.Join(",", pendingEvents) + "]";
            pendingEvents.Clear();
            return json;
        }
    }

    private string NumericMap() => "{" + String.Join(",", numeric.OrderBy(pair => pair.Key, StringComparer.Ordinal)
        .Select(pair => Quote(pair.Key) + ":" + Number(pair.Value))) + "}";

    private static string IntMap(Dictionary<string, int> values) => "{" + String.Join(",",
        values.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => Quote(pair.Key) + ":" + pair.Value)) + "}";

    private static string BoolMap(Dictionary<string, bool> values) => "{" + String.Join(",",
        values.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => Quote(pair.Key) + ":" + Bool(pair.Value))) + "}";

    private static string FloatMap(Dictionary<string, float> values) => "{" + String.Join(",",
        values.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => Quote(pair.Key) + ":" + Number(pair.Value))) + "}";

    private static string StringArray(IEnumerable<string> values) => "[" + String.Join(",",
        values.Select(Quote)) + "]";

    private void ReadArguments()
    {
        runId = ReadArgument("--gb-run-id=");
        nonce = ReadArgument("--gb-nonce=");
        buildDigest = ReadArgument("--gb-build-digest=");
        startScene = ReadArgument("--gb-start-scene=");
        Int32.TryParse(ReadArgument("--gb-level-count="), out declaredLevelCount);
        AddCsv(requiredRoles, ReadArgument("--gb-required-roles="));
        AddCsv(declaredNumeric, ReadArgument("--gb-numeric-slots="));
        AddCsv(supportedActions, ReadArgument("--gb-supported-actions="));
        AddCsv(supportedAxes, ReadArgument("--gb-supported-axes="));
        if (!Single.TryParse(
                ReadArgument("--gb-time-scale="), NumberStyles.Float,
                CultureInfo.InvariantCulture, out evaluatorTimeScale) ||
            evaluatorTimeScale < 1.0f || evaluatorTimeScale > 16.0f)
            evaluatorTimeScale = 1.0f;
        if (!Int32.TryParse(ReadArgument("--gb-observation-stride="), out observationStride) ||
            observationStride < 1 || observationStride > 120)
            observationStride = 1;
        requiredRoles.Add("gb_player");
        if (String.IsNullOrEmpty(runId) || String.IsNullOrEmpty(nonce) ||
            String.IsNullOrEmpty(buildDigest) || String.IsNullOrEmpty(startScene) ||
            declaredLevelCount <= 0 || supportedActions.Count == 0)
            throw new InvalidOperationException("observer protocol arguments are required");
    }

    private static void AddCsv(HashSet<string> target, string raw)
    {
        foreach (var value in (raw ?? "").Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
            target.Add(value.Trim().ToLowerInvariant());
    }

    private static string ReadArgument(string prefix)
    {
        foreach (var argument in Environment.GetCommandLineArgs())
            if (argument.StartsWith(prefix, StringComparison.Ordinal)) return argument.Substring(prefix.Length);
        return "";
    }

    private static int ReadIntArgument(string prefix)
    {
        if (!Int32.TryParse(ReadArgument(prefix), out var value) || value <= 0)
            throw new InvalidOperationException(prefix + " is required");
        return value;
    }

    private static bool SceneMatches(Scene scene, string path) =>
        String.Equals(scene.path, path, StringComparison.OrdinalIgnoreCase) ||
        String.Equals(scene.name, Path.GetFileNameWithoutExtension(path), StringComparison.OrdinalIgnoreCase);

    private static string Quote(string value)
    {
        if (value == null) return "\"\"";
        var builder = new StringBuilder(value.Length + 2).Append('"');
        foreach (var character in value)
        {
            switch (character)
            {
                case '\\': builder.Append("\\\\"); break;
                case '"': builder.Append("\\\""); break;
                case '\n': builder.Append("\\n"); break;
                case '\r': builder.Append("\\r"); break;
                case '\t': builder.Append("\\t"); break;
                default:
                    if (character < 32) builder.Append("\\u" + ((int)character).ToString("x4"));
                    else builder.Append(character);
                    break;
            }
        }
        return builder.Append('"').ToString();
    }

    private static string Number(double value) => value.ToString("R", CultureInfo.InvariantCulture);
    private static string Bool(bool value) => value ? "true" : "false";
    private static string Vector(Vector3 value) => "{\"x\":" + Number(value.x) +
        ",\"y\":" + Number(value.y) + ",\"z\":" + Number(value.z) + "}";
    private static string Hex(byte[] bytes)
    {
        var builder = new StringBuilder(bytes.Length * 2);
        foreach (var value in bytes) builder.Append(value.ToString("x2", CultureInfo.InvariantCulture));
        return builder.ToString();
    }

    private void OnDestroy()
    {
        stopping = true;
        Application.logMessageReceived -= OnLog;
        SceneManager.sceneLoaded -= OnSceneLoaded;
        GBOutcome.Reported -= OnOutcome;
        GBTelemetry.NumericReported -= OnNumeric;
        try { client?.Close(); } catch { }
    }
}
