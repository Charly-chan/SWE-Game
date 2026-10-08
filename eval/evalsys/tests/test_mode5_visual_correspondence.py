import base64
import io
from types import SimpleNamespace

import numpy as np
from PIL import Image

from evalsys.taskgen.mode5.visual_measure import measure_visual
from evalsys.taskgen.mode5.visual_reference import POLICY, image_features


def png(color, size):
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def run(name, captures):
    return SimpleNamespace(run_id=name, status="pass", reading={"visual_captures": captures})


def record(name, *, flat=False):
    before = png((20, 100, 220), (96, 54))
    removed = png((127, 127, 127), (96, 54))
    restored = png((20, 100, 220), (96, 54))
    ui_before = png((20, 100, 220), (480, 270))
    ui_removed = png((127, 127, 127), (480, 270))
    ui_restored = png((20, 100, 220), (480, 270))
    role = {
        "role": "gb_player", "before_png": before, "removed_png": removed,
        "restored_png": restored, "sprite_pngs": [png((20, 100, 220), (64, 64))],
        "materials_valid": True, "animation_state": "idle:0",
        "particle_count": 0,
    }
    return {"checkpoint_id": name, "run_id": "witness", "start_level": 0,
            "state": {"n": {"score": 0}},
            "record": {"schema": "gamebench.mode5.visual-capture.v1",
                       "checkpoint_id": name, "frame": 1,
                       "camera_active": True,
                       "thumbnail_png": png((127, 127, 127) if flat else (20, 100, 220), (96, 54)),
                       "ui_before_png": ui_before, "ui_removed_png": ui_removed,
                       "ui_restored_png": ui_restored, "roles": [role],
                       "audio_pcm16": "", "audio_sample_rate": 48000}}


def snapshot():
    reference_asset = image_features(Image.new("RGB", (96, 54), (20, 100, 220)))
    return {
        "obligations": {
            "visual.referenced_assets": ["gb_player"],
            "visual.ui_feedback": ["score"],
            "visual.render_configuration": ["camera", "renderer", "material"],
            "visual.animation_audio": ["animation", "audio", "particles", "gameplay_feedback"],
            "visual.reference_layout": ["level:1"],
        },
        "visual_reference": {"schema": "gamebench.mode5.visual-reference.v1",
            "complete": True, "policy": dict(POLICY), "assets": {"gb_player": [reference_asset]},
            "layouts": {},},
    }


def test_runtime_role_pixels_and_restore_are_admissible_without_vlm():
    witness = run("witness", [record("run_start"), record("run_end")])
    null = run("matched_null", [dict(record("run_start"), run_id="matched_null")])
    result = measure_visual(snapshot(), SimpleNamespace(witness=witness, hidden_behaviors=[], matched_null=null))
    assert result["vlm_used"] is False
    assert result["metric"] == "visual_implementation_correspondence"
    assert any(row["criterion"] == "visual.referenced_assets" for row in result["observations"])
    assert all(row["references"][0].startswith("controller/visual/") for row in result["observations"])


def test_flat_output_is_not_a_rendered_visual_pass():
    witness = run("witness", [record("run_start", flat=True)])
    null = run("matched_null", [dict(record("run_start"), run_id="matched_null")])
    result = measure_visual(snapshot(), SimpleNamespace(witness=witness, hidden_behaviors=[], matched_null=null))
    assert not any(row["criterion"] == "visual.render_configuration" for row in result["observations"])
    assert any(item["reason"] == "blank/flat actual output" for item in result["diagnostics"])
