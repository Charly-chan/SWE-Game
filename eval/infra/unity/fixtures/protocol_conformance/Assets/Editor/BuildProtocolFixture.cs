using System;
using System.IO;
using GameBench.Fixtures.Protocol;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace GameBench.Fixtures.Protocol.Editor
{
    public static class BuildProtocolFixture
    {
        public static void BuildLinuxPlayer()
        {
            var buildDirectory = Environment.GetEnvironmentVariable("GB_FIXTURE_BUILD_DIR");
            if (string.IsNullOrWhiteSpace(buildDirectory))
            {
                throw new InvalidOperationException("GB_FIXTURE_BUILD_DIR is required");
            }

            buildDirectory = Path.GetFullPath(buildDirectory);
            Directory.CreateDirectory(buildDirectory);
            Directory.CreateDirectory("Assets/Scenes");
            for (var index = 0; index < QualitySettings.names.Length; index++)
            {
                QualitySettings.SetQualityLevel(index, false);
                QualitySettings.vSyncCount = 0;
            }
            PlayerSettings.runInBackground = true;
            PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
            PlayerSettings.defaultScreenWidth = 960;
            PlayerSettings.defaultScreenHeight = 540;
            PlayerSettings.resizableWindow = false;

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var cameraObject = new GameObject("Main Camera");
            var camera = cameraObject.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.05f, 0.08f, 0.14f, 1.0f);
            cameraObject.tag = "MainCamera";
            new GameObject("GameBench Protocol Fixture").AddComponent<ProtocolFixtureRuntime>();

            const string scenePath = "Assets/Scenes/ProtocolFixture.unity";
            if (!EditorSceneManager.SaveScene(scene, scenePath))
            {
                throw new InvalidOperationException("Failed to save protocol fixture scene");
            }
            var executable = Path.Combine(buildDirectory, "ProtocolFixture.x86_64");
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = new[] { scenePath },
                locationPathName = executable,
                target = BuildTarget.StandaloneLinux64,
                options = BuildOptions.CleanBuildCache | BuildOptions.StrictMode
            });
            var summary = report.summary;
            File.WriteAllText(
                Path.Combine(buildDirectory, "build-result.json"),
                "{\"schema\":\"gamebench.unity-protocol-fixture-build.v1\"," +
                "\"result\":\"" + summary.result + "\"," +
                "\"errors\":" + summary.totalErrors + "," +
                "\"warnings\":" + summary.totalWarnings + "," +
                "\"unity_version\":\"" + Application.unityVersion + "\"}\n");
            if (summary.result != BuildResult.Succeeded || summary.totalErrors != 0)
            {
                throw new InvalidOperationException("Protocol fixture build failed: " + summary.result);
            }
        }
    }
}
