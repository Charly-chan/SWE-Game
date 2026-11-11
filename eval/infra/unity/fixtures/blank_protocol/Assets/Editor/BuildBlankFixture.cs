using System;
using System.IO;
using GameBench.Fixtures;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace GameBench.Fixtures.Editor
{
    public static class BuildBlankFixture
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
            AssetDatabase.SaveAssets();

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var cameraObject = new GameObject("Main Camera");
            var camera = cameraObject.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.035f, 0.075f, 0.13f, 1.0f);
            cameraObject.tag = "MainCamera";
            new GameObject("GameBench Blank Fixture").AddComponent<BlankFixtureRuntime>();

            const string scenePath = "Assets/Scenes/BlankFixture.unity";
            if (!EditorSceneManager.SaveScene(scene, scenePath))
            {
                throw new InvalidOperationException("Failed to save the blank fixture scene");
            }

            var executable = Path.Combine(buildDirectory, "BlankFixture.x86_64");
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
                "{\"schema\":\"gamebench.unity-blank-fixture-build.v1\"," +
                "\"result\":\"" + summary.result + "\"," +
                "\"errors\":" + summary.totalErrors + "," +
                "\"warnings\":" + summary.totalWarnings + "," +
                "\"unity_version\":\"" + Application.unityVersion + "\"}\n");

            if (summary.result != BuildResult.Succeeded || summary.totalErrors != 0)
            {
                throw new InvalidOperationException("Blank fixture Linux build failed: " + summary.result);
            }
        }
    }
}
