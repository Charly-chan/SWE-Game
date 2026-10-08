using System;
using System.IO;
using GameBench.Fixtures.CatDefense;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.InputSystem;
using GameBenchmark;

namespace GameBench.Fixtures.CatDefense.Editor
{
    public static class BuildCatDefenseFixture
    {
        public static void BuildLinuxPlayer()
        {
            ConfigureInputSystem();
            var buildDirectory = Environment.GetEnvironmentVariable("GB_FIXTURE_BUILD_DIR");
            if (string.IsNullOrWhiteSpace(buildDirectory)) throw new InvalidOperationException("GB_FIXTURE_BUILD_DIR is required");
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
            camera.backgroundColor = new Color(0.08f, 0.065f, 0.075f, 1.0f);
            cameraObject.tag = "MainCamera";
            camera.orthographic = true;
            camera.orthographicSize = 5;
            cameraObject.transform.position = new Vector3(0, 0, -10);
            var player = MarkerCube("Player", "gb_player", "player", new Vector3(-2, 0, 0));
            var enemy = MarkerCube("Enemy", "gb_enemy", "enemy", Vector3.zero);
            var goal = MarkerCube("Goal", "gb_goal", "goal", new Vector3(3, 0, 0));
            var projectile = MarkerCube("Projectile", "gb_projectile", "projectile", Vector3.zero);
            projectile.SetActive(false);
            var pad = MarkerCube("BuildPad", "gb_interactive", "pad", new Vector3(-2, 0, 0));
            pad.transform.localScale = new Vector3(2, .2f, 1);
            MarkerCube("FrontPad", "gb_interactive", "front-pad", new Vector3(-1, 0, 0));
            var actions = AssetDatabase.LoadAssetAtPath<InputActionAsset>(
                "Assets/GameBenchmarkSDK/GameBenchmarkInput.inputactions");
            if (actions == null) throw new InvalidOperationException("fixed InputAction asset is missing");
            var playerInput = player.AddComponent<PlayerInput>();
            playerInput.actions = actions;
            playerInput.defaultActionMap = "Player";
            playerInput.neverAutoSwitchControlSchemes = true;
            var runtime = new GameObject("Behavior Causality Gameplay Fixture").AddComponent<CatDefenseFixtureRuntime>();
            runtime.playerInput = playerInput;
            runtime.enemy = enemy;
            runtime.projectile = projectile;
            runtime.goal = goal;
            const string scenePath = "Assets/Scenes/CatDefenseFixture.unity";
            if (!EditorSceneManager.SaveScene(scene, scenePath)) throw new InvalidOperationException("Failed to save behavior-causality fixture scene");

            var executable = Path.Combine(buildDirectory, "CatDefenseFixture.x86_64");
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
                "{\"schema\":\"gamebench.unity-behavior-causality-fixture-build.v1\"," +
                "\"result\":\"" + summary.result + "\",\"errors\":" + summary.totalErrors + "," +
                "\"warnings\":" + summary.totalWarnings + ",\"unity_version\":\"" + Application.unityVersion + "\"}\n");
            if (summary.result != BuildResult.Succeeded || summary.totalErrors != 0)
                throw new InvalidOperationException("Behavior-causality fixture build failed: " + summary.result);
        }

        private static void ConfigureInputSystem()
        {
            var assets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/ProjectSettings.asset");
            if (assets == null || assets.Length == 0) throw new InvalidOperationException("ProjectSettings unavailable");
            var serialized = new SerializedObject(assets[0]);
            var property = serialized.FindProperty("activeInputHandler");
            if (property == null) throw new InvalidOperationException("activeInputHandler unavailable");
            property.intValue = 1;
            serialized.ApplyModifiedPropertiesWithoutUndo();
            AssetDatabase.SaveAssets();
        }

        private static GameObject MarkerCube(string name, string role, string stableId, Vector3 position)
        {
            var value = GameObject.CreatePrimitive(PrimitiveType.Cube);
            value.name = name;
            value.transform.position = position;
            var marker = value.AddComponent<GBEntity>();
            var serialized = new SerializedObject(marker);
            serialized.FindProperty("role").stringValue = role;
            serialized.FindProperty("stableId").stringValue = stableId;
            serialized.ApplyModifiedPropertiesWithoutUndo();
            return value;
        }
    }
}
