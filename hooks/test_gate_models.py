"""Opus/Sonnet/Fable on a non-subagents agent needs a written reason in the description (0.8.0, 0.8.1)."""
from test_gate import denied, run

ag = lambda **kw: {"tool_name": "Agent", "tool_input": {"prompt": "x", "description": "d", **kw}}


def test_expensive_model_without_reason_is_denied(tmp_path):
    for st in ("general-purpose", "", "Explore", "Plan", "claude"):
        for m in ("opus", "sonnet", "fable", "OPUS", "claude-opus-5-5"):
            out = run("pre", ag(subagent_type=st, model=m), tmp_path)
            assert denied(out), (st, m)
            assert "costs several times" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_reason_tag_lets_it_through(tmp_path):
    assert run("pre", ag(subagent_type="general-purpose", model="opus",
                         description="[opus: code review of the patch] review"), tmp_path) is None
    assert run("pre", ag(model="sonnet", description="Survey [sonnet: needs judgement on design]"), tmp_path) is None


def test_bad_tags_are_denied(tmp_path):
    for d in ("[opus:] x", "[opus:   ] x", "[sonnet: why] x", "opus: why", "[opus:]]", "[opus: ] unrelated [x]", None):
        assert denied(run("pre", ag(model="opus", description=d), tmp_path)), d


def test_cheap_models_and_own_agents_pass(tmp_path):
    assert run("pre", ag(subagent_type="general-purpose", model="haiku"), tmp_path) is None
    assert run("pre", ag(subagent_type="subagents:reader", model="opus"), tmp_path) is None
    out = run("pre", ag(subagent_type="fork", model="opus"), tmp_path)  # fork ignores model
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert "additionalContext" in run("pre", ag(subagent_type="fork"), tmp_path)["hookSpecificOutput"]
    assert run("pre", {**ag(model="opus"), "agent_id": "a1"}, tmp_path) is None  # subagent's own call
