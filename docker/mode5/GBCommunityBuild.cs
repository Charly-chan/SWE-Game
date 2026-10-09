using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;

namespace GameBench.Community.Editor
{
    public static class SelfCheckBuild
    {
        public static void BuildLinuxPlayer()
        {
            var output = Environment.GetEnvironmentVariable("GB_UNITY_BUILD_OUTPUT");
            if (String.IsNullOrWhiteSpace(output))
                throw new InvalidOperationException("GB_UNITY_BUILD_OUTPUT is required");

            var scenes = EditorBuildSettings.scenes
                .Where(scene => scene.enabled && File.Exists(scene.path))
                .Select(scene => scene.path)
                .ToArray();
            if (scenes.Length == 0)
            {
                scenes = AssetDatabase.FindAssets("t:Scene")
                    .Select(AssetDatabase.GUIDToAssetPath)
                    .Where(path => path.StartsWith("Assets/", StringComparison.Ordinal))
                    .OrderBy(path => path, StringComparer.Ordinal)
                    .ToArray();
            }
            if (scenes.Length == 0)
                throw new InvalidOperationException("No buildable scene exists under Assets/");

            var parent = Path.GetDirectoryName(output);
            if (!String.IsNullOrEmpty(parent))
                Directory.CreateDirectory(parent);
            var backend = Environment.GetEnvironmentVariable("GB_UNITY_SCRIPTING_BACKEND");
            if (String.Equals(backend, "Mono", StringComparison.OrdinalIgnoreCase))
                PlayerSettings.SetScriptingBackend(
                    NamedBuildTarget.Standalone, ScriptingImplementation.Mono2x
                );
            else if (String.Equals(backend, "IL2CPP", StringComparison.OrdinalIgnoreCase))
                PlayerSettings.SetScriptingBackend(
                    NamedBuildTarget.Standalone, ScriptingImplementation.IL2CPP
                );
            else
                throw new InvalidOperationException(
                    "GB_UNITY_SCRIPTING_BACKEND must be Mono or IL2CPP"
                );
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = scenes,
                locationPathName = output,
                target = BuildTarget.StandaloneLinux64,
                options = BuildOptions.None,
            });
            if (report.summary.result != BuildResult.Succeeded)
                throw new InvalidOperationException(
                    "StandaloneLinux64 build failed: " + report.summary.result
                );
        }
    }
}
