using System;
using System.Collections;
using System.IO;
using UnityEngine;

namespace GameBench.Fixtures
{
    public sealed class BlankFixtureRuntime : MonoBehaviour
    {
        private const int Width = 960;
        private const int Height = 540;
        private string _outputDirectory = string.Empty;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void ConfigureHeadlessDisplay()
        {
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = 60;
        }

        private void Awake()
        {
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = 60;
            _outputDirectory = ReadOutputDirectory();
            Directory.CreateDirectory(_outputDirectory);
            Debug.Log("GameBench blank fixture awake");
        }

        private IEnumerator Start()
        {
            Debug.Log("GameBench blank fixture capture started");
            for (var frame = 0; frame < 10; frame++)
            {
                yield return null;
            }

            yield return new WaitForEndOfFrame();

            var screenshot = new Texture2D(Width, Height, TextureFormat.RGB24, false);
            screenshot.ReadPixels(new Rect(0, 0, Width, Height), 0, 0);
            screenshot.Apply(false, false);
            File.WriteAllBytes(
                Path.Combine(_outputDirectory, "blank-fixture.png"),
                screenshot.EncodeToPNG());
            Destroy(screenshot);

            File.WriteAllText(
                Path.Combine(_outputDirectory, "runtime-result.json"),
                "{\"schema\":\"gamebench.unity-blank-fixture-runtime.v1\"," +
                "\"status\":\"passed\",\"width\":960,\"height\":540," +
                "\"unity_version\":\"" + Escape(Application.unityVersion) + "\"}\n");

            Debug.Log("GameBench blank fixture passed");

            yield return null;
            Application.Quit(0);
        }

        private void OnGUI()
        {
            var previous = GUI.color;
            GUI.color = Color.white;
            GUI.Box(new Rect(300, 210, 360, 120), "GameBench Unity Blank Fixture");
            GUI.Label(new Rect(374, 270, 250, 24), "6000.3.23f1 / Linux x86_64");
            GUI.color = previous;
        }

        private static string ReadOutputDirectory()
        {
            const string prefix = "--gb-output=";
            foreach (var argument in Environment.GetCommandLineArgs())
            {
                if (argument.StartsWith(prefix, StringComparison.Ordinal))
                {
                    return Path.GetFullPath(argument.Substring(prefix.Length));
                }
            }

            return Path.Combine(Application.persistentDataPath, "gamebench-blank-fixture");
        }

        private static string Escape(string value)
        {
            return value.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }
    }
}
