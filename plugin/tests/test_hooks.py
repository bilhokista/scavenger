import json
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).parent.parent / "hooks"


def run_script(name, payload):
    proc = subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_session_reminds_skill_first():
    code, out, _ = run_script("session.py", {})
    assert code == 0
    assert "GOAL.md" in out
    assert "evidence" in out.lower()


def test_gate_denies_without_goal(tmp_path):
    (tmp_path / ".scavenger").mkdir()
    (tmp_path / ".scavenger" / "active_mission").write_text("demo", encoding="utf-8")
    code, out, _ = run_script(
        "gate.py",
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "src/work.py"},
            "cwd": str(tmp_path),
        },
    )
    assert code == 0
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "GOAL.md" in decision["permissionDecisionReason"]


def test_gate_allows_goal_writes(tmp_path):
    (tmp_path / ".scavenger").mkdir()
    (tmp_path / ".scavenger" / "active_mission").write_text("demo", encoding="utf-8")
    code, out, _ = run_script(
        "gate.py",
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "missions/demo/GOAL.md"},
            "cwd": str(tmp_path),
        },
    )
    assert code == 0
    assert out.strip() == ""


def test_gate_allows_valid_goal(tmp_path):
    (tmp_path / ".scavenger").mkdir()
    (tmp_path / ".scavenger" / "active_mission").write_text("demo", encoding="utf-8")
    goal_dir = tmp_path / "missions" / "demo"
    goal_dir.mkdir(parents=True)
    (goal_dir / "GOAL.md").write_text(
        "+++\n"
        'statement = "rent of 1500 USD due"\n'
        "[settle]\n"
        'adapter = "payment_email"\n'
        "[budget]\n"
        'money = "50.00"\ntokens = 10\nrounds = 5\n'
        "[target]\n"
        'amount = "1500.00"\ncurrency = "USD"\n'
        "deadline = 2030-01-01T00:00:00Z\n"
        "+++\n\nBody.\n",
        encoding="utf-8",
    )
    code, out, _ = run_script(
        "gate.py",
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/work.py"},
            "cwd": str(tmp_path),
        },
    )
    assert code == 0
    assert out.strip() == ""


def test_gate_passes_bash_through(tmp_path):
    code, out, _ = run_script(
        "gate.py",
        {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/x"},
            "cwd": str(tmp_path),
        },
    )
    assert code == 0
    assert out.strip() == ""


def test_evidence_blocks_claim_without_proof():
    code, out, _ = run_script(
        "evidence.py",
        {
            "last_message": "Done, the bounty is submitted.",
            "stop_hook_active": False,
        },
    )
    assert code == 2
    assert "evidence" in out.lower()


def test_evidence_passes_claim_with_proof():
    code, out, _ = run_script(
        "evidence.py",
        {
            "last_message": "Done, submitted PR https://github.com/o/r/pull/7.",
            "stop_hook_active": False,
        },
    )
    assert code == 0
    assert out.strip() == ""


def test_evidence_passes_plain_chat():
    code, _out, _ = run_script(
        "evidence.py",
        {
            "last_message": "Halo, lagi mikir dulu.",
            "stop_hook_active": False,
        },
    )
    assert code == 0


def test_evidence_never_blocks_twice():
    code, _out, _ = run_script(
        "evidence.py",
        {
            "last_message": "Done, submitted.",
            "stop_hook_active": True,
        },
    )
    assert code == 0
