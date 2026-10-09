// Injected ONLY by the independent evaluator. No goals or scores in this peer.
// A renderer census is not a visibility measurement: remove each role's native
// renderers and retain before/removed/restored pixels for controller analysis.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using GameBenchmark;
using UnityEngine;
using UnityEngine.SceneManagement;

[Serializable] public sealed class GBVisualRoleCapture
{
    public string role;
    public string before_png;
    public string removed_png;
    public string restored_png;
    public string[] sprite_pngs;
    public bool materials_valid;
    public string animation_state;
    public int particle_count;
}
[Serializable] public sealed class GBVisualCaptureRecord
{
    public string schema = "gamebench.mode5.visual-capture.v1";
    public string status = "measured";
    public string scene;
    public string checkpoint_id;
    public int frame;
    public bool camera_active;
    public string thumbnail_png;
    public string ui_before_png;
    public string ui_removed_png;
    public string ui_restored_png;
    public GBVisualRoleCapture[] roles;
    public string audio_pcm16;
    public int audio_sample_rate;
    public string error;
}

public sealed class GameBenchmarkEvaluatorAudioTap : MonoBehaviour
{
    private readonly object guard = new object();
    private short[] recent = new short[0];
    private void OnAudioFilterRead(float[] data, int channels)
    {
        if (channels <= 0) return;
        var count = Math.Min(data.Length / channels, 4096);
        var samples = new short[count];
        for (var i = 0; i < count; i++)
        {
            double value = 0;
            for (var channel = 0; channel < channels; channel++) value += data[i * channels + channel];
            value /= channels;
            samples[i] = (short)(Math.Max(-1.0, Math.Min(1.0, value)) * 32767);
        }
        lock (guard) recent = samples;
    }
    public string ReadPCM()
    {
        lock (guard)
        {
            var bytes = new byte[recent.Length * 2];
            for (var i = 0; i < recent.Length; i++)
            {
                bytes[2 * i] = (byte)(recent[i] & 255);
                bytes[2 * i + 1] = (byte)((recent[i] >> 8) & 255);
            }
            return Convert.ToBase64String(bytes);
        }
    }
}

public static class GameBenchmarkEvaluatorVisualCapture
{
    private static readonly HashSet<int> tapped = new HashSet<int>();
    public static void AttachAudioTaps()
    {
        foreach (var listener in UnityEngine.Object.FindObjectsByType<AudioListener>(FindObjectsSortMode.None))
            if (listener.enabled && tapped.Add(listener.GetInstanceID()))
                listener.gameObject.AddComponent<GameBenchmarkEvaluatorAudioTap>();
    }

    private static string Pixels(int width = 96, int height = 54)
    {
        Texture2D full = null;
        Texture2D small = null;
        RenderTexture target = null;
        var previous = RenderTexture.active;
        try
        {
            full = new Texture2D(Screen.width, Screen.height, TextureFormat.RGB24, false);
            full.ReadPixels(new Rect(0, 0, Screen.width, Screen.height), 0, 0);
            full.Apply(false, false);
            target = RenderTexture.GetTemporary(width, height, 0, RenderTextureFormat.ARGB32);
            Graphics.Blit(full, target);
            RenderTexture.active = target;
            small = new Texture2D(width, height, TextureFormat.RGB24, false);
            small.ReadPixels(new Rect(0, 0, width, height), 0, 0);
            small.Apply(false, false);
            return Convert.ToBase64String(small.EncodeToPNG());
        }
        finally
        {
            RenderTexture.active = previous;
            if (target != null) RenderTexture.ReleaseTemporary(target);
            if (full != null) UnityEngine.Object.Destroy(full);
            if (small != null) UnityEngine.Object.Destroy(small);
        }
    }

    private static string SpritePixels(Sprite sprite)
    {
        RenderTexture target = null;
        Texture2D readable = null;
        var previous = RenderTexture.active;
        try
        {
            if (sprite == null || sprite.texture == null) return "";
            var rect = sprite.textureRect;
            // Read the used sprite region, not the complete atlas. GPU-backed
            // textures need not be marked readable by the candidate.
            var texture = sprite.texture;
            target = RenderTexture.GetTemporary(64, 64, 0, RenderTextureFormat.ARGB32);
            Graphics.Blit(texture, target,
                new Vector2(rect.width / texture.width, rect.height / texture.height),
                new Vector2(rect.x / texture.width, rect.y / texture.height));
            RenderTexture.active = target;
            readable = new Texture2D(64, 64, TextureFormat.RGBA32, false);
            readable.ReadPixels(new Rect(0, 0, 64, 64), 0, 0);
            readable.Apply(false, false);
            return Convert.ToBase64String(readable.EncodeToPNG());
        }
        catch { return ""; }
        finally
        {
            RenderTexture.active = previous;
            if (target != null) RenderTexture.ReleaseTemporary(target);
            if (readable != null) UnityEngine.Object.Destroy(readable);
        }
    }

    public static IEnumerator Capture(string checkpoint, int frame, IEnumerable<string> requiredRoles,
        Action<GBVisualCaptureRecord> completed)
    {
        var record = new GBVisualCaptureRecord { checkpoint_id = checkpoint, frame = frame,
            scene = SceneManager.GetActiveScene().path };
        var roles = new List<GBVisualRoleCapture>();
        var entities = UnityEngine.Object.FindObjectsByType<GBEntity>(FindObjectsSortMode.None)
            .Where(entity => entity.enabled && entity.gameObject.activeInHierarchy).ToArray();
        record.camera_active = Camera.allCameras.Any(camera => camera.enabled && camera.gameObject.activeInHierarchy);
        // Time is frozen by the probe during this coroutine. Unscaled-time
        // effects remain possible and are rejected by the restore-drift test.
        yield return new WaitForEndOfFrame();
        record.thumbnail_png = Pixels();
        foreach (var role in requiredRoles.OrderBy(value => value).Take(24))
        {
            var owned = entities.Where(entity => entity.Role == role).ToArray();
            var renderers = owned.SelectMany(entity => entity.GetComponentsInChildren<Renderer>())
                .Where(renderer => renderer.enabled && renderer.gameObject.activeInHierarchy &&
                    owned.Contains(renderer.GetComponentInParent<GBEntity>())).Distinct().Take(128).ToArray();
            if (renderers.Length == 0) continue;
            var sample = new GBVisualRoleCapture { role = role, before_png = Pixels(),
                materials_valid = renderers.All(renderer => renderer.sharedMaterials.All(material =>
                    material != null && material.shader != null && material.shader.isSupported &&
                    material.shader.name != "Hidden/InternalErrorShader")),
                sprite_pngs = renderers.OfType<SpriteRenderer>().Select(renderer => SpritePixels(renderer.sprite))
                    .Where(value => value.Length > 0).Distinct().Take(4).ToArray(),
                animation_state = String.Join(";", owned.SelectMany(entity => entity.GetComponentsInChildren<Animator>())
                    .Where(animator => animator.enabled && animator.runtimeAnimatorController != null)
                    .Select(animator => animator.GetCurrentAnimatorStateInfo(0).fullPathHash + ":" +
                        animator.GetCurrentAnimatorStateInfo(0).normalizedTime.ToString("F3", System.Globalization.CultureInfo.InvariantCulture))),
                particle_count = owned.SelectMany(entity => entity.GetComponentsInChildren<ParticleSystem>())
                    .Where(system => system.isPlaying).Sum(system => system.particleCount) };
            foreach (var renderer in renderers) renderer.enabled = false;
            yield return new WaitForEndOfFrame();
            sample.removed_png = Pixels();
            foreach (var renderer in renderers) if (renderer != null) renderer.enabled = true;
            yield return new WaitForEndOfFrame();
            sample.restored_png = Pixels();
            roles.Add(sample);
        }
        record.roles = roles.ToArray();
        // Native Canvas and IMGUI both participate. Merely finding a Text
        // component or candidate string does not become readability evidence.
        var canvases = UnityEngine.Object.FindObjectsByType<Canvas>(FindObjectsSortMode.None)
            .Where(canvas => canvas.enabled).ToArray();
        var immediate = UnityEngine.Object.FindObjectsByType<MonoBehaviour>(FindObjectsSortMode.None)
            .Where(component => component.enabled && component.GetType().GetMethod("OnGUI",
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic) != null).Take(128).ToArray();
        record.ui_before_png = Pixels(480, 270);
        foreach (var canvas in canvases) canvas.enabled = false;
        foreach (var component in immediate) component.enabled = false;
        yield return new WaitForEndOfFrame();
        record.ui_removed_png = Pixels(480, 270);
        foreach (var canvas in canvases) if (canvas != null) canvas.enabled = true;
        foreach (var component in immediate) if (component != null) component.enabled = true;
        yield return new WaitForEndOfFrame();
        record.ui_restored_png = Pixels(480, 270);
        var tap = UnityEngine.Object.FindObjectsByType<GameBenchmarkEvaluatorAudioTap>(FindObjectsSortMode.None).FirstOrDefault();
        record.audio_pcm16 = tap == null ? "" : tap.ReadPCM();
        record.audio_sample_rate = AudioSettings.outputSampleRate;
        completed(record);
    }
}
