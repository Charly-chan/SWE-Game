using System;
using GameBenchmark;
using GameBench.Fixtures.Observer;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.InputSystem;

namespace GameBench.Fixtures.Observer.Editor
{
    public static class BuildObserverFixture
    {
        private const string ScenePath = "Assets/Game/Scenes/ObserverFixture.unity";

        public static void BuildLinuxPlayer()
        {
            ConfigureInputSystem();
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            var cameraObject = new GameObject("Camera");
            var camera = cameraObject.AddComponent<Camera>();
            camera.orthographic = true;
            camera.orthographicSize = 5;
            cameraObject.transform.position = new Vector3(0, 0, -10);

            var player = MarkerCube("Player", "gb_player", "player", new Vector3(-2, 0, 0));
            var enemy = MarkerCube("Enemy", "gb_enemy", "enemy", Vector3.zero);
            var goal = MarkerCube("Goal", "gb_goal", "goal", new Vector3(3, 0, 0));
            var projectile = MarkerCube("Projectile", "gb_projectile", "projectile", Vector3.zero);
            projectile.SetActive(false);

            var actions = AssetDatabase.LoadAssetAtPath<InputActionAsset>(
                "Assets/GameBenchmarkSDK/GameBenchmarkInput.inputactions"
            );
            if (actions == null) throw new InvalidOperationException("fixed InputAction asset is missing");
            var playerInput = player.AddComponent<PlayerInput>();
            playerInput.actions = actions;
            playerInput.defaultActionMap = "Player";
            playerInput.neverAutoSwitchControlSchemes = true;

            var runtime = new GameObject("ObserverFixtureRuntime").AddComponent<ObserverFixtureRuntime>();
            runtime.playerInput = playerInput;
            runtime.enemy = enemy;
            runtime.projectile = projectile;
            runtime.goal = goal;

            System.IO.Directory.CreateDirectory("Assets/Game/Scenes");
            EditorSceneManager.SaveScene(scene, ScenePath);
            var outputRoot = Environment.GetEnvironmentVariable("GB_FIXTURE_BUILD_DIR");
            if (String.IsNullOrWhiteSpace(outputRoot))
                throw new InvalidOperationException("GB_FIXTURE_BUILD_DIR is required");
            System.IO.Directory.CreateDirectory(outputRoot);
            PlayerSettings.runInBackground = true;
            PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
            PlayerSettings.defaultScreenWidth = 960;
            PlayerSettings.defaultScreenHeight = 540;
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = new[] { ScenePath },
                locationPathName = System.IO.Path.Combine(outputRoot, "ObserverFixture.x86_64"),
                target = BuildTarget.StandaloneLinux64,
                options = BuildOptions.None,
            });
            System.IO.File.WriteAllText(
                System.IO.Path.Combine(outputRoot, "build-result.json"),
                "{\"schema\":\"gamebench.unity-observer-fixture-build.v1\",\"result\":\"" +
                report.summary.result + "\",\"errors\":" + report.summary.totalErrors +
                ",\"warnings\":" + report.summary.totalWarnings +
                ",\"unity_version\":\"" + Application.unityVersion + "\"}\n"
            );
            if (report.summary.result != BuildResult.Succeeded)
                throw new Exception("Observer fixture build failed: " + report.summary.result);
        }

        private static GameObject MarkerCube(string name, string role, string stableId, Vector3 position)
        {
            var gameObject = GameObject.CreatePrimitive(PrimitiveType.Cube);
            gameObject.name = name;
            gameObject.transform.position = position;
            var marker = gameObject.AddComponent<GBEntity>();
            var serialized = new SerializedObject(marker);
            serialized.FindProperty("role").stringValue = role;
            serialized.FindProperty("stableId").stringValue = stableId;
            serialized.ApplyModifiedPropertiesWithoutUndo();
            return gameObject;
        }

        private static void ConfigureInputSystem()
        {
            var assets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/ProjectSettings.asset");
            if (assets == null || assets.Length == 0)
                throw new InvalidOperationException("ProjectSettings asset is unavailable");
            var serialized = new SerializedObject(assets[0]);
            var property = serialized.FindProperty("activeInputHandler");
            if (property == null)
                throw new InvalidOperationException("activeInputHandler ProjectSetting is unavailable");
            property.intValue = 1;
            serialized.ApplyModifiedPropertiesWithoutUndo();
            AssetDatabase.SaveAssets();
        }
    }
}
