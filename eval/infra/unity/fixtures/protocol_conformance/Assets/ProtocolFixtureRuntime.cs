using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using UnityEngine;

namespace GameBench.Fixtures.Protocol
{
    public sealed class ProtocolFixtureRuntime : MonoBehaviour
    {
        private const string Protocol = "gamebench.unity-controller.v1";
        private const int MaxMessageBytes = 1024 * 1024;
        private readonly ConcurrentQueue<Command> _commands = new ConcurrentQueue<Command>();
        private readonly object _sendLock = new object();
        private TcpClient _client;
        private NetworkStream _stream;
        private Thread _reader;
        private string _runId = string.Empty;
        private string _nonce = string.Empty;
        private string _buildDigest = string.Empty;
        private float _playerX;
        private int _observationSequence;
        private bool _stopping;

        [Serializable]
        private sealed class Command
        {
            public string type;
            public int sequence;
            public string canonical_action;
            public float value;
            public int hold_frames;
            public string checkpoint_id;
            public string reason;
        }

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void Configure()
        {
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = 60;
        }

        private IEnumerator Start()
        {
            ReadArguments();
            for (var frame = 0; frame < 5; frame++) yield return null;
            _client = new TcpClient(AddressFamily.InterNetwork);
            _client.Connect(IPAddress.Loopback, ReadIntArgument("--gb-controller-port="));
            _stream = _client.GetStream();
            Send("{\"type\":\"hello\",\"protocol\":\"" + Protocol +
                 "\",\"run_id\":\"" + Escape(_runId) + "\",\"nonce\":\"" + Escape(_nonce) +
                 "\",\"build_digest\":\"" + Escape(_buildDigest) + "\"}");
            Send("{\"type\":\"ready\",\"scene\":\"ProtocolFixture\",\"frame\":" + Time.frameCount + "}");
            _reader = new Thread(ReadLoop) { IsBackground = true, Name = "GameBenchProtocolReader" };
            _reader.Start();
        }

        private void Update()
        {
            while (_commands.TryDequeue(out var command))
            {
                if (command.type == "action") HandleAction(command);
                else if (command.type == "capture") StartCoroutine(HandleCapture(command));
                else if (command.type == "stop")
                {
                    _stopping = true;
                    Send("{\"type\":\"stop_ack\",\"reason\":\"" +
                         Escape(command.reason ?? "") + "\"}");
                    Application.Quit(0);
                }
            }
        }

        private void HandleAction(Command command)
        {
            var startFrame = Time.frameCount;
            if (command.canonical_action == "gb_right" && command.value > 0.0f)
            {
                _playerX += command.value * Math.Max(1, command.hold_frames);
            }
            Send("{\"type\":\"action_ack\",\"sequence\":" + command.sequence +
                 ",\"start_frame\":" + startFrame + ",\"end_frame\":" + Time.frameCount + "}");
            _observationSequence++;
            var x = _playerX.ToString("R", CultureInfo.InvariantCulture);
            Send("{\"type\":\"observation\",\"sequence\":" + _observationSequence +
                 ",\"row\":{\"f\":" + Time.frameCount +
                 ",\"g\":{\"gb_player\":1},\"o\":{},\"px\":" + x +
                 ",\"py\":0.0,\"pz\":0.0,\"n\":{\"progress\":" + x +
                 "},\"wgc\":false,\"lv\":1,\"d\":{},\"c\":[],\"so\":\"\"," +
                 "\"vx\":1.0,\"vy\":0.0,\"vz\":0.0,\"s\":{\"numeric\":{\"progress\":" + x +
                 "},\"audio_events\":0,\"anim\":0,\"node_count\":2,\"visible_count\":1,\"text\":1}}," +
                 "\"events\":[{\"kind\":\"action_observed\",\"action\":\"gb_right\"}]}");
        }

        private IEnumerator HandleCapture(Command command)
        {
            yield return new WaitForEndOfFrame();
            var texture = new Texture2D(960, 540, TextureFormat.RGB24, false);
            texture.ReadPixels(new Rect(0, 0, 960, 540), 0, 0);
            texture.Apply(false, false);
            var png = texture.EncodeToPNG();
            Destroy(texture);
            using var sha = SHA256.Create();
            var digest = ToHex(sha.ComputeHash(png));
            Send("{\"type\":\"capture_ack\",\"sequence\":" + command.sequence +
                 ",\"frame\":" + Time.frameCount + ",\"checkpoint_id\":\"" + Escape(command.checkpoint_id) +
                 "\",\"bytes_digest\":\"sha256:" + digest + "\",\"png_base64\":\"" +
                 Convert.ToBase64String(png) + "\"}");
        }

        private void ReadLoop()
        {
            try
            {
                while (!_stopping)
                {
                    var payload = Receive();
                    var command = JsonUtility.FromJson<Command>(payload);
                    if (command == null || string.IsNullOrEmpty(command.type)) throw new InvalidDataException("invalid command");
                    _commands.Enqueue(command);
                }
            }
            catch (Exception exception)
            {
                if (!_stopping)
                {
                    try { Send("{\"type\":\"fatal\",\"error\":\"" + Escape(exception.Message) + "\"}"); }
                    catch { }
                }
            }
        }

        private void Send(string json)
        {
            var payload = Encoding.UTF8.GetBytes(json);
            if (payload.Length <= 0 || payload.Length > MaxMessageBytes) throw new InvalidDataException("message size");
            var prefix = BitConverter.GetBytes(IPAddress.HostToNetworkOrder(payload.Length));
            lock (_sendLock)
            {
                _stream.Write(prefix, 0, prefix.Length);
                _stream.Write(payload, 0, payload.Length);
                _stream.Flush();
            }
        }

        private string Receive()
        {
            var prefix = ReadExact(4);
            var size = IPAddress.NetworkToHostOrder(BitConverter.ToInt32(prefix, 0));
            if (size <= 0 || size > MaxMessageBytes) throw new InvalidDataException("message size");
            return Encoding.UTF8.GetString(ReadExact(size));
        }

        private byte[] ReadExact(int size)
        {
            var buffer = new byte[size];
            var offset = 0;
            while (offset < size)
            {
                var read = _stream.Read(buffer, offset, size - offset);
                if (read <= 0) throw new EndOfStreamException("controller channel closed");
                offset += read;
            }
            return buffer;
        }

        private void ReadArguments()
        {
            _runId = ReadArgument("--gb-run-id=");
            _nonce = ReadArgument("--gb-nonce=");
            _buildDigest = ReadArgument("--gb-build-digest=");
            if (string.IsNullOrEmpty(_runId) || string.IsNullOrEmpty(_nonce) || string.IsNullOrEmpty(_buildDigest))
                throw new InvalidOperationException("protocol arguments are required");
        }

        private static string ReadArgument(string prefix)
        {
            foreach (var argument in Environment.GetCommandLineArgs())
                if (argument.StartsWith(prefix, StringComparison.Ordinal)) return argument.Substring(prefix.Length);
            return string.Empty;
        }

        private static int ReadIntArgument(string prefix)
        {
            if (!int.TryParse(ReadArgument(prefix), out var value) || value <= 0) throw new InvalidOperationException(prefix);
            return value;
        }

        private static string Escape(string value) => value.Replace("\\", "\\\\").Replace("\"", "\\\"");
        private static string ToHex(byte[] bytes)
        {
            var builder = new StringBuilder(bytes.Length * 2);
            foreach (var value in bytes) builder.Append(value.ToString("x2"));
            return builder.ToString();
        }

        private void OnGUI()
        {
            GUI.Box(new Rect(260 + _playerX * 12.0f, 220, 440, 100), "GameBench Unity Controller v1");
            GUI.Label(new Rect(390, 270, 240, 24), "gb_right observed: " + _playerX.ToString("0.0"));
        }

        private void OnDestroy()
        {
            _stopping = true;
            try { _client?.Close(); } catch { }
        }
    }
}
