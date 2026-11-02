"""background.py: main-agent shell commands always run in the background (0.11.0)."""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).with_name("background.py")


def run(ev, raw=None):
    data = raw if raw is not None else json.dumps(ev)
    r = subprocess.run([sys.executable, str(HOOK)], input=data.encode("utf-8"), capture_output=True)
    assert r.returncode == 0
    out = r.stdout.decode("utf-8").strip()
    return json.loads(out) if out else None


def test_main_shell_command_goes_to_background():
    for tool in ("Bash", "PowerShell"):
        out = run({"tool_name": tool, "tool_input": {"command": "ls", "timeout": 5000}})
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["updatedInput"] == {"command": "ls", "timeout": 5000, "run_in_background": True}, tool
        assert "permissionDecision" not in hso  # normal permission flow still applies


def test_subagent_and_already_background_untouched():
    assert run({"tool_name": "Bash", "agent_id": "a1", "tool_input": {"command": "ls"}}) is None
    assert run({"tool_name": "Bash", "tool_input": {"command": "ls", "run_in_background": True}}) is None


def test_other_tools_and_bad_input_untouched():
    assert run({"tool_name": "Read", "tool_input": {"file_path": "x"}}) is None
    assert run(None, raw="not json") is None


def test_opt_out_env():
    import os
    env = {**os.environ, "SUBAGENTS_BACKGROUND": "off"}
    r = subprocess.run([sys.executable, str(HOOK)], input=b'{"tool_name":"Bash","tool_input":{"command":"ls"}}',
                       capture_output=True, env=env)
    assert r.stdout.strip() == b""
