"""Tests for the decided-edit reminder in gate.py: run with `python -m pytest hooks/test_gate_edits.py`."""
from test_gate import run


def edit(text="x = 1\n", session="s1", **extra):
    return {"tool_name": "Edit", "session_id": session,
            "tool_input": {"file_path": "a.py", "old_string": "y", "new_string": text}, **extra}


def reminded(out):
    return bool(out) and "subagents:editor" in out["hookSpecificOutput"].get("additionalContext", "")


def test_third_small_edit_in_a_row_reminds(tmp_path):
    assert run("post", edit(), tmp_path) is None
    assert run("post", edit(), tmp_path) is None
    out = run("post", edit(), tmp_path)
    assert reminded(out) and out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


def test_reminds_on_every_third_write_until_the_editor_is_used(tmp_path):
    outs = [run("post", edit(), tmp_path) for _ in range(6)]
    assert [reminded(o) for o in outs] == [False, False, True, False, False, True]


def test_big_write_reminds_at_once(tmp_path):
    ev = {"tool_name": "Write", "session_id": "s1", "tool_input": {"file_path": "t.py", "content": "a\n" * 60}}
    assert reminded(run("post", ev, tmp_path))


def test_multiedit_counts_every_new_string(tmp_path):
    ev = {"tool_name": "MultiEdit", "session_id": "s1",
          "tool_input": {"file_path": "a.py", "edits": [{"new_string": "a\n" * 25}, {"new_string": "b\n" * 25}]}}
    assert reminded(run("post", ev, tmp_path))


def test_subagent_edits_are_not_counted(tmp_path):
    for _ in range(4):
        assert run("post", edit(agent_id="a1"), tmp_path) is None


def test_editor_delegation_resets_the_streak(tmp_path):
    run("post", edit(), tmp_path)
    run("post", edit(), tmp_path)
    agent = {"tool_name": "Agent", "session_id": "s1", "tool_input": {"subagent_type": "subagents:editor"}}
    assert run("pre", agent, tmp_path) is None
    assert run("post", edit(), tmp_path) is None


def test_sessions_are_counted_apart(tmp_path):
    run("post", edit(session="s1"), tmp_path)
    run("post", edit(session="s1"), tmp_path)
    assert run("post", edit(session="s2"), tmp_path) is None


def test_scripted_file_writes_in_the_shell_count(tmp_path):
    for cmd in ("sed -i 's/a/b/' x.py", "python - <<'EOF'\nopen(p,'w').write(t)\nEOF"):
        ev = {"tool_name": "Bash", "session_id": "s-" + cmd[:3], "tool_input": {"command": cmd},
              "tool_response": {"stdout": "ok"}}
        run("post", ev, tmp_path)
        run("post", ev, tmp_path)
        assert reminded(run("post", ev, tmp_path)), cmd


def test_plain_shell_commands_do_not_count(tmp_path):
    ev = {"tool_name": "Bash", "session_id": "s1", "tool_input": {"command": "git status > /dev/null"},
          "tool_response": {"stdout": "ok"}}
    for _ in range(4):
        assert run("post", ev, tmp_path) is None


def test_streak_threshold_from_environment(tmp_path):
    assert reminded(run("post", edit(), tmp_path, SUBAGENTS_EDIT_STREAK="1"))
