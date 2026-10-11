"""Tests for gate.py: run with `python -m pytest hooks/test_gate.py`."""
import json
import os
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).with_name("gate.py")


def run(phase, ev, tmp_path, raw=None, **env):
    e = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), **env}
    data = raw if raw is not None else json.dumps(ev)
    r = subprocess.run([sys.executable, str(GATE), phase], input=data.encode("utf-8"), capture_output=True, env=e)
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode("utf-8")
    return json.loads(out) if out.strip() else None


def denied(out):
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


def make(tmp_path, name="big.log", kb=60, line=80):
    p = tmp_path / name
    row = "x" * (line - 1) + "\n"
    p.write_text(row * (kb * 1024 // line), encoding="utf-8")
    return p


def read(path, **kw):
    return {"tool_name": "Read", "tool_input": {"file_path": str(path), **kw}}


def test_big_whole_read_denied_and_logged(tmp_path):
    out = run("pre", read(make(tmp_path)), tmp_path)
    assert denied(out) and "subagents:reader" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert (tmp_path / ".claude" / "subagents-gate.jsonl").exists()


def test_huge_limit_is_not_a_bypass_but_small_range_passes(tmp_path):
    p = make(tmp_path)
    assert denied(run("pre", read(p, offset=1, limit=999999), tmp_path))
    assert denied(run("pre", read(p, offset=0), tmp_path))
    assert run("pre", read(p, offset=100, limit=200), tmp_path) is None


def test_small_missing_image_and_subagent_pass(tmp_path):
    p = make(tmp_path)
    assert run("pre", read(make(tmp_path, "small.txt", kb=10)), tmp_path) is None
    assert run("pre", read(tmp_path / "nope.txt"), tmp_path) is None
    assert run("pre", read(make(tmp_path, "pic.png", kb=900)), tmp_path) is None
    assert run("pre", {**read(p), "agent_id": "a1"}, tmp_path) is None


def test_ipynb_is_gated(tmp_path):
    assert denied(run("pre", read(make(tmp_path, "nb.ipynb", kb=200)), tmp_path))


def test_relative_and_gitbash_paths(tmp_path):
    p = make(tmp_path)
    ev = {"tool_name": "Read", "cwd": str(tmp_path), "tool_input": {"file_path": "big.log"}}
    assert denied(run("pre", ev, tmp_path))
    if os.name == "nt":
        drive, rest = str(p).split(":", 1)
        gb = "/" + drive.lower() + rest.replace("\\", "/")
        assert denied(run("pre", read(gb), tmp_path))


def test_cat_dump_denied_but_limited_or_small_pass(tmp_path):
    p = make(tmp_path)
    bash = lambda c: {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": c}}
    assert denied(run("pre", bash(f'cat "{p}"'), tmp_path))
    assert denied(run("pre", {"tool_name": "PowerShell", "tool_input": {"command": f'Get-Content "{p}"'}}, tmp_path))
    assert run("pre", bash(f'cat "{p}" | head -50'), tmp_path) is None
    assert run("pre", bash(f'cat "{make(tmp_path, "s.txt", kb=5)}"'), tmp_path) is None
    assert run("pre", bash("ls -la"), tmp_path) is None


def test_web_and_browser_variants(tmp_path):
    for tool, agent in [("WebFetch", "subagents:web"), ("mcp__Claude_Browser__get_page_text", "subagents:browser"),
                        ("mcp__claude-in-chrome__read_page", "subagents:browser"),
                        ("mcp__remote-devices__Claude_Browser__get_page_text", "subagents:browser")]:
        out = run("pre", {"tool_name": tool, "tool_input": {}}, tmp_path)
        assert denied(out) and agent in out["hookSpecificOutput"]["permissionDecisionReason"]
        assert run("pre", {"tool_name": tool, "agent_id": "a1", "tool_input": {}}, tmp_path) is None
    assert run("pre", {"tool_name": "WebSearch", "tool_input": {"query": "x"}}, tmp_path) is None
    assert run("pre", {"tool_name": "mcp__Claude_Browser__find", "tool_input": {}}, tmp_path) is None


def test_post_counts_text_not_metadata(tmp_path):
    ev = {"tool_name": "Bash", "tool_input": {"command": "x"}, "tool_response": {"stdout": "y" * 30000, "stderr": ""}}
    assert "subagents:runner" in run("post", ev, tmp_path)["hookSpecificOutput"]["additionalContext"]
    ev["tool_response"] = {"stdout": "ok", "meta": "m" * 40000}
    assert run("post", ev, tmp_path) is None
    ev["tool_response"] = "z" * 30000
    assert run("post", ev, tmp_path) is not None


def test_modes_bad_input_bad_env_and_korean_path(tmp_path):
    p = make(tmp_path, "한글 로그.log")
    assert denied(run("pre", read(p), tmp_path))
    assert run("pre", read(p), tmp_path, SUBAGENTS_GATE="observe") is None
    assert run("pre", read(p), tmp_path, SUBAGENTS_GATE="off") is None
    assert denied(run("pre", read(p), tmp_path, SUBAGENTS_READ_KB="abc"))  # bad env falls back to the default
    for raw in ["not json", "[]", '{"tool_name": "Read", "tool_input": "str"}', '{"tool_name": "Read", "tool_input": {"file_path": null}}']:
        assert run("pre", None, tmp_path, raw=raw) is None


def test_log_masks_secrets(tmp_path):
    ev = {"tool_name": "Bash", "tool_input": {"command": 'curl -H "Authorization: Bearer abc123" x'},
          "tool_response": {"stdout": "y" * 30000}}
    run("post", ev, tmp_path)
    text = (tmp_path / ".claude" / "subagents-gate.jsonl").read_text(encoding="utf-8")
    assert "abc123" not in text
