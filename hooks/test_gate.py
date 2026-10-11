"""Tests for gate.py: run with `python -m pytest hooks/test_gate.py`."""
import json
import os
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).with_name("gate.py")


def run(phase, ev, tmp_path, **env):
    e = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), **env}
    r = subprocess.run([sys.executable, str(GATE), phase], input=json.dumps(ev), capture_output=True, text=True, env=e)
    assert r.returncode == 0
    return json.loads(r.stdout) if r.stdout.strip() else None


def big(tmp_path, kb=50):
    p = tmp_path / "big.log"
    p.write_text("x" * (kb * 1024))
    return str(p)


def test_whole_read_of_big_file_denied(tmp_path):
    (tmp_path / ".claude").mkdir()
    out = run("pre", {"tool_name": "Read", "tool_input": {"file_path": big(tmp_path)}}, tmp_path)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "subagents:reader" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert (tmp_path / ".claude" / "subagents-gate.jsonl").exists()


def test_ranged_small_subagent_and_missing_pass(tmp_path):
    p = big(tmp_path)
    assert run("pre", {"tool_name": "Read", "tool_input": {"file_path": p, "limit": 50}}, tmp_path) is None
    assert run("pre", {"tool_name": "Read", "agent_id": "a1", "tool_input": {"file_path": p}}, tmp_path) is None
    assert run("pre", {"tool_name": "Read", "tool_input": {"file_path": big(tmp_path, 10)}}, tmp_path) is None
    assert run("pre", {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "nope")}}, tmp_path) is None


def test_observe_and_off_modes(tmp_path):
    p = big(tmp_path)
    assert run("pre", {"tool_name": "Read", "tool_input": {"file_path": p}}, tmp_path, SUBAGENTS_GATE="observe") is None
    assert run("pre", {"tool_name": "Read", "tool_input": {"file_path": p}}, tmp_path, SUBAGENTS_GATE="off") is None


def test_post_reminder_only_for_big_output(tmp_path):
    ev = {"tool_name": "Bash", "tool_input": {"command": "cat x"}, "tool_response": {"stdout": "y" * 30000}}
    out = run("post", ev, tmp_path)
    assert "subagents:runner" in out["hookSpecificOutput"]["additionalContext"]
    ev["tool_response"] = {"stdout": "ok"}
    assert run("post", ev, tmp_path) is None


def test_web_and_browser_reads_denied_for_main_only(tmp_path):
    for tool, agent in [("WebFetch", "subagents:web"), ("WebSearch", "subagents:web"),
                        ("mcp__Claude_Browser__get_page_text", "subagents:browser")]:
        out = run("pre", {"tool_name": tool, "tool_input": {}}, tmp_path)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and agent in out["hookSpecificOutput"]["permissionDecisionReason"]
        assert run("pre", {"tool_name": tool, "agent_id": "a1", "tool_input": {}}, tmp_path) is None


def test_bad_stdin_fails_open(tmp_path):
    r = subprocess.run([sys.executable, str(GATE), "pre"], input="not json", capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == ""
