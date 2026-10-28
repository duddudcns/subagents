"""Any explicit model passes; no reason tag is needed since 0.10.0 (the panel already shows the model)."""
from test_gate import run

ag = lambda **kw: {"tool_name": "Agent", "tool_input": {"prompt": "x", "description": "d", **kw}}


def test_any_explicit_model_passes_without_reason(tmp_path):
    for st in ("general-purpose", "", "Explore", "Plan", "claude"):
        for m in ("haiku", "opus", "sonnet", "fable", "claude-opus-5-5"):
            assert run("pre", ag(subagent_type=st, model=m), tmp_path) is None, (st, m)


def test_own_agents_and_fork(tmp_path):
    assert run("pre", ag(subagent_type="subagents:reader", model="opus"), tmp_path) is None
    out = run("pre", ag(subagent_type="fork", model="opus"), tmp_path)  # fork ignores model
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert run("pre", {**ag(model="opus"), "agent_id": "a1"}, tmp_path) is None  # subagent's own call
