from pathlib import Path
import subprocess
ROOT = Path(__file__).resolve().parents[3]

def test_single_task_runner_resolves_repo_outside_its_working_directory(tmp_path):
    result = subprocess.run(['bash', str(ROOT / 'scripts/run_coding.sh'), 'wizard_chase', 'brief', 'codex', '--help'], cwd=tmp_path, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'usage: ./scripts/run_coding.sh' in result.stdout
