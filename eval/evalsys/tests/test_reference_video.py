"""Shared video extraction preserves caller-owned files and prior sequences."""
from unittest.mock import patch
from pathlib import Path
import shutil
import subprocess

import pytest

from evalsys.scard.reference_video import extract_reference_frames


def test_extraction_never_clears_caller_directory(tmp_path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"source")
    retained = tmp_path / "existing-evidence.txt"
    retained.write_text("retain", encoding="utf-8")
    with patch("evalsys.scard.reference_video._tool", return_value=None):
        assert extract_reference_frames(source, tmp_path) == ((), ())
        assert extract_reference_frames(source, tmp_path) == ((), ())
    assert source.read_bytes() == b"source"
    assert retained.read_text(encoding="utf-8") == "retain"
    assert len(list(tmp_path.glob("sequence-*"))) == 2


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe required")
def test_extracts_real_frames_without_removing_prior_files(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=red:s=64x64:r=30", "-t", "2", str(source)],
                   check=True, timeout=30)
    marker = tmp_path / "keep.txt"
    marker.write_text("prior evidence", encoding="utf-8")
    frames, times = extract_reference_frames(source, tmp_path, max_frames=3)
    assert len(frames) == len(times) == 3
    assert all(Path(frame).is_file() and Path(frame).parent.parent == tmp_path for frame in frames)
    assert source.is_file() and marker.read_text(encoding="utf-8") == "prior evidence"
