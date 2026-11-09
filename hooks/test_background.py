"""background.py: main-agent shell commands default to the background; an explicit boolean is respected (0.12.0)."""
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


def test_explicit_foreground_is_respected():
    # 0.12.0: only an absent (or non-boolean) value is defaulted; the agent can ask for foreground on a quick command.
    for tool in ("Bash", "PowerShell"):
        assert run({"tool_name": tool, "tool_input": {"command": "ls", "run_in_background": False}}) is None, tool
    # the decision does not depend on the command text
    assert run({"tool_name": "Bash", "tool_input": {"command": "npm test", "run_in_background": False}}) is None


def test_non_boolean_value_is_treated_as_absent():
    # Only a real boolean is the agent's choice; null/0/"true" behave as before (forced to background).
    for bad in (None, 0, 1, "true", "false"):
        out = run({"tool_name": "Bash", "tool_input": {"command": "ls", "run_in_background": bad}})
        assert out["hookSpecificOutput"]["updatedInput"] == {"command": "ls", "run_in_background": True}, bad


def test_subagent_explicit_false_untouched():
    assert run({"tool_name": "Bash", "agent_id": "a1", "tool_input": {"command": "ls", "run_in_background": False}}) is None


def test_absent_key_still_defaults_to_background_and_keeps_fields():
    out = run({"tool_name": "Bash", "tool_input": {"command": "npm test", "description": "d", "timeout": 1}})
    assert out["hookSpecificOutput"]["updatedInput"] == {
        "command": "npm test", "description": "d", "timeout": 1, "run_in_background": True}


def test_other_tools_and_bad_input_untouched():
    assert run({"tool_name": "Read", "tool_input": {"file_path": "x"}}) is None
    assert run(None, raw="not json") is None


def test_opt_out_env():
    import os
    env = {**os.environ, "SUBAGENTS_BACKGROUND": "off"}
    r = subprocess.run([sys.executable, str(HOOK)], input=b'{"tool_name":"Bash","tool_input":{"command":"ls"}}',
                       capture_output=True, env=env)
    assert r.stdout.strip() == b""
