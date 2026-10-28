"""Regression tests for the round-2 review findings (advisor + Codex on v0.4.1)."""
import json

from test_gate import denied, make, read, run


def test_tail_read_with_offset_passes(tmp_path):
    p = make(tmp_path, kb=400)  # about 5120 lines of 80 bytes
    assert run("pre", read(p, offset=5000), tmp_path) is None  # about 120 lines left
    assert run("pre", read(p, offset=999999, limit=999999), tmp_path) is None  # nothing to return
    assert denied(run("pre", read(p, offset=10), tmp_path))  # 2000 lines, about 160 KB


def test_one_huge_line_counts_with_read_cap(tmp_path):
    p = tmp_path / "wide.txt"
    p.write_text("a" * 80_000 + "\n" + "b\n" * 10_000, encoding="utf-8")
    assert run("pre", read(p, offset=1, limit=1), tmp_path) is None  # Read cuts the line at 2000 chars


def test_shell_dump_variants(tmp_path):
    p = make(tmp_path)
    bash = lambda c: {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": c}}
    for c in [f'cat "{p}"; true', f'cd "{tmp_path}" && cat big.log', f'cat "{p}" 2>&1', f'cat "{p}" | grep .',
              "cat *.log"]:
        assert denied(run("pre", bash(c), tmp_path)), c
    other = tmp_path / "elsewhere"
    other.mkdir()
    away = {"tool_name": "Bash", "cwd": str(other), "tool_input": {"command": f'cd "{tmp_path}" && cat big.log; true'}}
    assert denied(run("pre", away, tmp_path))  # cd is followed, not the session cwd
    for c in [f'cat "{p}" | tail -5', f'cat "{p}" | wc -l', "echo hi; ls"]:
        assert run("pre", bash(c), tmp_path) is None, c


def test_post_ignores_unknown_shapes_and_bad_env(tmp_path):
    ev = {"tool_name": "Bash", "tool_input": {"command": "x"}, "tool_response": {"meta": "x" * 30000}}
    assert run("post", ev, tmp_path) is None
    p = make(tmp_path)
    for bad in ["inf", "nan", "-5", "0"]:
        assert denied(run("pre", read(p), tmp_path, SUBAGENTS_READ_KB=bad)), bad


def test_log_stays_valid_json_and_masks_quoted_secrets(tmp_path):
    for cmd in ['curl --password "two words" x', 'curl -H "Authorization: Bearer abc123" x', "export API_KEY=zzz9"]:
        ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "tool_response": {"stdout": "y" * 30000}}
        run("post", ev, tmp_path)
    lines = (tmp_path / ".claude" / "subagents-gate.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([json.loads(l) for l in lines]) == 3  # every line parses
    text = "\n".join(lines)
    for secret in ["two words", "abc123", "zzz9"]:
        assert secret not in text, secret


def test_agent_advice_only_for_main_model_delegations(tmp_path):
    ag = lambda **kw: {"tool_name": "Agent", "tool_input": {"prompt": "x", **kw}}
    out = run("pre", ag(subagent_type="general-purpose"), tmp_path)
    assert "subagents:reader" in out["hookSpecificOutput"]["additionalContext"]
    assert "permissionDecision" not in out["hookSpecificOutput"]  # advice, never a deny
    assert run("pre", ag(), tmp_path) is not None  # default type is general-purpose
    assert run("pre", ag(subagent_type="subagents:reader"), tmp_path) is None
    assert run("pre", ag(subagent_type="general-purpose", model="haiku"), tmp_path) is None
    assert run("pre", {**ag(subagent_type="Explore"), "agent_id": "a1"}, tmp_path) is None


def test_untyped_explore_and_plan_are_denied(tmp_path):
    ag = lambda **kw: {"tool_name": "Agent", "tool_input": {"prompt": "x", **kw}}
    for st in ("Explore", "Plan"):
        out = run("pre", ag(subagent_type=st), tmp_path)
        assert denied(out), st  # their built-in definition inherits the main model, ignoring CLAUDE_CODE_SUBAGENT_MODEL
        assert "subagents:reader" in out["hookSpecificOutput"]["permissionDecisionReason"]
        assert run("pre", ag(subagent_type=st, model="haiku"), tmp_path) is None  # explicit model passes
    assert "permissionDecision" not in run("pre", ag(subagent_type="general-purpose"), tmp_path)["hookSpecificOutput"]


def test_agent_advice_mentions_small_batches(tmp_path):
    out = run("pre", {"tool_name": "Agent", "tool_input": {"prompt": "x"}}, tmp_path)
    assert "small batch" in out["hookSpecificOutput"]["additionalContext"]
