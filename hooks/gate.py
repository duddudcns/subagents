"""Delegation gate for the main agent. Calls made by subagents are never gated.

pre: PreToolUse on Read, WebFetch, WebSearch, and the browser page readers (get_page_text, read_page).
  Web and browser page reads by main are always denied, with a pointer to subagents:web or subagents:browser.
  A whole-file Read (no offset and no limit) of a file of SUBAGENTS_READ_KB or more is denied. The default is 40 KB,
  about 10k tokens. The deny message points to subagents:reader or a ranged read.
post: PostToolUse on Bash, PowerShell and Grep.
  Output of SUBAGENTS_OUT_KB or more (default 20 KB) only adds a reminder. The output is already in the context by then,
  so it cannot be blocked; the reminder steers the next similar call.

Every decision is appended to ~/.claude/subagents-gate.jsonl.
SUBAGENTS_GATE=off disables the gate. SUBAGENTS_GATE=observe logs decisions without acting on them.
"""
import json
import os
import sys
import time
from pathlib import Path

MODE = os.environ.get("SUBAGENTS_GATE", "on")
READ_KB = float(os.environ.get("SUBAGENTS_READ_KB", 40))
OUT_KB = float(os.environ.get("SUBAGENTS_OUT_KB", 20))
LOG = Path.home() / ".claude" / "subagents-gate.jsonl"
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ipynb")


def log(rec):
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": MODE, **rec}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def size_kb(resp) -> float:
    if resp is None:
        return 0.0
    s = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
    return len(s.encode("utf-8", "ignore")) / 1024


def emit(obj):
    sys.stdout.write(json.dumps(obj))


DELEGATE_ALWAYS = {
    "WebFetch": "subagents:web",
    "WebSearch": "subagents:web",
    "mcp__Claude_Browser__get_page_text": "subagents:browser",
    "mcp__Claude_Browser__read_page": "subagents:browser",
}


def deny(reason):
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                 "permissionDecisionReason": reason}})


def pre(ev, inp):
    tool = ev.get("tool_name", "")
    if tool in DELEGATE_ALWAYS:
        # Page and search content is bulk by nature; main only needs the findings.
        agent = DELEGATE_ALWAYS[tool]
        log({"phase": "pre", "tool": tool, "action": "deny", "to": agent})
        if MODE != "observe":
            deny(f"{tool} output is bulk content that would stay in the main context. Delegate it to the {agent} agent "
                 "with the exact question and what counts as an answer; review its findings yourself.")
        return
    if tool != "Read" or inp.get("offset") is not None or inp.get("limit") is not None:
        return
    path = inp.get("file_path", "")
    try:
        kb = os.path.getsize(path) / 1024
    except OSError:
        return
    if path.lower().endswith(SKIP_EXT) or kb < READ_KB:
        return
    log({"phase": "pre", "tool": "Read", "path": path, "kb": round(kb, 1), "action": "deny"})
    if MODE == "observe":
        return
    reason = (f"{path} is {kb:.0f} KB (about {kb * 256:.0f} tokens). Read whole, it would stay in the main context and be "
              "re-read on every later call. Delegate it to the subagents:reader agent with the exact question, or Read "
              "a range with offset/limit if you need specific lines for your own judgement.")
    deny(reason)


def post(ev, inp):
    kb = size_kb(ev.get("tool_response"))
    if kb < OUT_KB:
        return
    tool = ev.get("tool_name", "")
    log({"phase": "post", "tool": tool, "kb": round(kb, 1), "action": "remind",
         "cmd": str(inp.get("command") or inp.get("pattern") or "")[:200]})
    if MODE == "observe":
        return
    msg = (f"That {tool} output was {kb:.0f} KB and now sits in the main context. For similar long-output work, use "
           "subagents:runner for commands or subagents:reader for searches, and get back only the result.")
    emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}})


def main():
    if MODE == "off":
        return
    try:
        ev = json.load(sys.stdin)
    except ValueError:
        return  # fail open
    if ev.get("agent_id"):
        return  # a subagent's own call
    inp = ev.get("tool_input") or {}
    (post if len(sys.argv) > 1 and sys.argv[1] == "post" else pre)(ev, inp)


if __name__ == "__main__":
    main()
