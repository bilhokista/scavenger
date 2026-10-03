import subprocess
import sys


def test_version_flag_prints_version():
    proc = subprocess.run(
        [sys.executable, "-m", "scavenger.cli", "--version"],
        capture_output=True,
        text=True,
        check=False,
        cwd="daemon",
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "0.1.0.dev0"
