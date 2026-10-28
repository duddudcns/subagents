"""Every Agent call outside subagents:* and fork must name its model (0.9.0)."""
from test_gate import denied, run

ag = lambda **kw: {"tool_name": "Agent", "tool_input": {"prompt": "x", "description": "d", **kw}}


def test_missing_model_is_denied(tmp_path):
    for st in ("general-purpose", "", "claude", "anti-slop:slop-detector", "Explore", "Plan"):
        out = run("pre", ag(subagent_type=st), tmp_path)
        assert denied(out), st
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        assert 'explicit model' in reason and '"haiku"' in reason, st
        assert "subagents:reader" in reason, st


def test_missing_model_reason_mentions_small_batches(tmp_path):
    out = run("pre", ag(subagent_type="general-purpose"), tmp_path)
    assert "small batch" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_named_model_and_own_agents_pass(tmp_path):
    for st in ("general-purpose", "", "claude", "anti-slop:slop-detector"):
        assert run("pre", ag(subagent_type=st, model="haiku"), tmp_path) is None, st
    assert run("pre", ag(subagent_type="subagents:reader"), tmp_path) is None


def test_fork_without_model_still_only_advised(tmp_path):
    out = run("pre", ag(subagent_type="fork"), tmp_path)  # fork ignores model, so requiring one is pointless
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert "small batch" in out["hookSpecificOutput"]["additionalContext"]


def test_subagent_own_call_not_gated(tmp_path):
    assert run("pre", {**ag(subagent_type="general-purpose"), "agent_id": "a1"}, tmp_path) is None
