"""Delegation gate for the main agent. Calls made by subagents (input carries agent_id) are never gated.

pre (PreToolUse):
- Agent/Task calls without a subagents:* type or an explicit model get a reminder to use the Haiku agents for
  mechanical work and to keep each worker's batch small. Explore and Plan without a model are denied: their built-in
  definition inherits the main model and ignores CLAUDE_CODE_SUBAGENT_MODEL (measured 2026-10-19).
- WebFetch, and the browser page readers get_page_text / read_page in every browser variant, are always denied.
  They are bulk by nature, so they go to subagents:web or subagents:browser.
- A Read whose returned bytes reach SUBAGENTS_READ_KB (default 40 KB) is denied. The bytes are counted for the
  requested lines (offset, then limit with a default of 2000), with each line capped at Read's 2000-character cut.
  Counting stops at the threshold. Images and PDFs are exempt, because main may need to see them.
- The plain shell dump `cat/type/Get-Content/gc FILE...` of a big file is denied in any `;`/`&&`/`||` segment,
  unless the segment is piped into head/tail/wc/Select-Object -First/-Last. Other dump forms get the post reminder.
post (PostToolUse on Bash, PowerShell, Grep, Edit, Write, MultiEdit, NotebookEdit):
- Output text of SUBAGENTS_OUT_KB or more (default 20 KB) adds a reminder. It cannot block, because the call already ran.
- Main's own file writes are counted per session: Edit/Write/MultiEdit/NotebookEdit, and shell commands that write
  files from a script (sed -i, open(..., 'w'), write_text, Set-Content, ...). Every SUBAGENTS_EDIT_STREAK-th write
  (default 3) since the last Agent call to subagents:editor, or one write of SUBAGENTS_EDIT_LINES new lines or more
  (default 40), adds a reminder to hand a decided change to subagents:editor. Main kept writing decided
  edits itself (tests from given cases, removing debug code, the same patch in several files) on 2026-10-22.

Every decision is appended to ~/.claude/subagents-gate.jsonl. Secrets are masked before serialisation, and the log
rotates at 5 MB. SUBAGENTS_GATE=off disables the gate; SUBAGENTS_GATE=observe logs decisions without acting on them.
Any error lets the call through: the gate fails open and always exits 0.
"""
import glob
import json
import math
import os
import re
import shlex
import sys
import time
from pathlib import Path

LOG = Path.home() / ".claude" / "subagents-gate.jsonl"
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf")
READ_DEFAULT_LINES = 2000
READ_LINE_CAP = 2000  # Read truncates longer lines to this many characters
DELEGATE_ALWAYS = re.compile(r"^(WebFetch|mcp__.*(Claude_Browser|claude-in-chrome).*__(get_page_text|read_page))$")
DUMP_CMD = re.compile(r"^\s*(cat|type|get-content|gc)\s+(.+?)\s*$", re.I)
# Only filters that bound the output size count; grep/sed/awk can pass everything through.
PIPE_LIMITERS = re.compile(r"\|\s*(head|tail|wc)\b|\|\s*(select|select-object)\s+-(first|last)\b|-(totalcount|tail|head)\b",
                           re.I)
SEGMENT_SPLIT = re.compile(r"\s*(?:;|&&|\|\||\n)\s*")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
# Shell forms that write a file from a script; a plain `> file` redirect is too common (logs, /dev/null) to count.
SCRIPT_WRITE = re.compile(r"\bsed\s+(-\w*\s+)*-i|\bopen\([^)]*['\"][wa]b?['\"]|\.write_(text|bytes)\(|"
                          r"\b(set-content|add-content|out-file)\b|\bperl\s+-\w*i", re.I)
SECRET = re.compile(r"""(?ix)(authorization|bearer|api[_-]?key|access[_-]?key|token|passw(?:or)?d|secret)
                        (["'=:\s]+)(?:(?:bearer|basic)\s+)?("[^"]*"|'[^']*'|[^\s"']+)""")


def env_kb(name, default):
    """A finite positive number from the environment, else the default."""
    try:
        v = float(os.environ.get(name, default))
    except ValueError:
        return float(default)
    return v if math.isfinite(v) and v > 0 else float(default)


def mask(v):
    """Mask secrets inside every string of a log record, before it is serialised."""
    if isinstance(v, str):
        return SECRET.sub(lambda m: m.group(1) + m.group(2) + "***", v)
    if isinstance(v, dict):
        return {k: mask(x) for k, x in v.items()}
    if isinstance(v, list):
        return [mask(x) for x in v]
    return v


def log(rec):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > 5_000_000:
            LOG.replace(LOG.with_suffix(".jsonl.1"))
        line = json.dumps(mask({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": MODE, **rec}), ensure_ascii=False)
        with LOG.open("a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def emit(obj):
    sys.stdout.write(json.dumps(obj))


def deny(reason):
    if MODE != "observe":
        emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": reason}})


def resolve(path, cwd):
    """Absolute Path for Windows, Git Bash (/c/...) and relative paths (relative to the session cwd)."""
    p = str(path).strip().strip('"').strip("'")
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m and os.name == "nt":
        p = f"{m.group(1)}:/{m.group(2)}"
    q = Path(p).expanduser()
    if not q.is_absolute() and cwd:
        q = Path(cwd) / q
    return q


def read_estimate_kb(path, offset, limit):
    """KB of lines offset..offset+limit-1 (1-based), each capped like Read caps it; stops at the threshold."""
    if path.stat().st_size < 1024 * READ_KB:  # cannot reach the threshold
        return path.stat().st_size / 1024
    total, cap = 0, 1024 * READ_KB
    with path.open("rb") as f:
        for i, line in enumerate(f, 1):
            if i < offset:
                continue
            if i >= offset + limit:
                break
            total += min(len(line), READ_LINE_CAP * 4)  # at most 4 bytes per character
            if total >= cap:
                break
    return total / 1024


def dump_kb(segment, cwd):
    """KB that one command segment dumps in the plain `cat/type/gc FILE...` form; bounded pipes are exempt."""
    if PIPE_LIMITERS.search(segment):
        return 0.0
    m = DUMP_CMD.match(segment.split("|")[0])
    if not m:
        return 0.0
    try:
        args = shlex.split(re.sub(r"\s*\d?>.*$", "", m.group(2)), posix=os.name != "nt")
    except ValueError:
        return 0.0
    files = []
    for a in args:
        if a.startswith("-"):
            continue
        p = resolve(a, cwd)
        files += [Path(x) for x in glob.glob(str(p))] if any(c in a for c in "*?[") else [p]
    return sum(f.stat().st_size for f in files if f.is_file()) / 1024


def pre(ev, inp, cwd):
    tool = ev.get("tool_name", "")
    if DELEGATE_ALWAYS.match(tool):
        agent = "subagents:web" if tool == "WebFetch" else "subagents:browser"
        log({"phase": "pre", "tool": tool, "action": "deny", "to": agent})
        deny(f"{tool} returns bulk page content that would stay in the main context. Delegate it to the {agent} agent "
             "with the exact question and what counts as an answer, then review its findings yourself. For a small "
             "check on a page you can also use the browser's find tool.")
        return
    if tool in ("Agent", "Task"):
        st, model = str(inp.get("subagent_type") or ""), str(inp.get("model") or "")
        if st == "subagents:editor":
            reset_writes(ev)
        if st.startswith("subagents:") or model:
            return  # already a Haiku agent, or the model was chosen on purpose (e.g. a review)
        if st in INHERITING_TYPES:
            # Their built-in definition inherits the main model, so CLAUDE_CODE_SUBAGENT_MODEL does not reach them.
            log({"phase": "pre", "tool": tool, "subagent_type": st, "action": "deny"})
            deny(f"'{st}' without a model runs on the main model: its built-in definition inherits it and ignores "
                 f"CLAUDE_CODE_SUBAGENT_MODEL. For reading or surveying use subagents:reader. If this needs '{st}' "
                 "itself, call it again with an explicit model (model: \"haiku\", or \"sonnet\" for judgement).")
            return
        log({"phase": "pre", "tool": tool, "subagent_type": st or "general-purpose", "action": "advise"})
        if MODE != "observe":
            emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
                  f"This delegates to '{st or 'general-purpose'}', which runs on the main model or that agent's own default "
                  "model, with its full tool set. If the task is mechanical "
                  "(reading/summarising files or logs, applying decided edits, running commands, web lookups, page "
                  "checks), use subagents:reader / editor / runner / web / browser instead: Haiku with trimmed tools, "
                  "a fraction of the cost. "
                  "Keep this agent only for work that needs judgement. For a long job, give each agent a small batch "
                  "(a few files or functions) and start a fresh one for the next: an agent re-reads its growing "
                  "context every turn, so one long-lived worker costs far more than several short ones."}})
        return
    if tool == "Read":
        path = resolve(inp.get("file_path", ""), cwd)
        if path.suffix.lower() in SKIP_EXT or not path.is_file():
            return
        limit, offset = inp.get("limit"), inp.get("offset")
        limit = int(limit) if isinstance(limit, (int, float)) and limit > 0 else READ_DEFAULT_LINES
        offset = int(offset) if isinstance(offset, (int, float)) and offset > 1 else 1
        kb = read_estimate_kb(path, offset, limit)
        if kb < READ_KB:
            return
        log({"phase": "pre", "tool": tool, "path": str(path), "kb": round(kb, 1), "offset": offset, "limit": limit,
             "action": "deny"})
        deny(f"This Read would return about {kb:.0f} KB or more (about {kb * 256:.0f}+ tokens) of {path.name}. It would "
             "stay in the main context and be re-read on every later call. Delegate it to the subagents:reader agent "
             "with the exact question. If you need specific lines for your own judgement, Read a smaller range with "
             f"offset/limit, below about {READ_KB:.0f} KB.")
        return
    if tool in ("Bash", "PowerShell"):
        cmd = str(inp.get("command", ""))
        kb, here = 0.0, cwd
        for seg in SEGMENT_SPLIT.split(cmd):
            cd = re.match(r"^\s*(?:cd|set-location|sl|pushd)\s+(.+?)\s*$", seg, re.I)
            if cd:  # follow `cd DIR && cat FILE` so relative paths resolve where the shell will be
                here = str(resolve(cd.group(1), here))
                continue
            kb += dump_kb(seg, here)
        if kb < READ_KB:
            return
        log({"phase": "pre", "tool": tool, "cmd": cmd[:200], "kb": round(kb, 1), "action": "deny"})
        deny(f"This command dumps about {kb:.0f} KB of file content into the main context. Delegate it to the "
             "subagents:reader agent with the exact question. If you need specific lines, use Read with offset/limit.")


def response_text(resp):
    """Only the text that reaches the model (stdout/stderr/output/content/...); unknown shapes count as no text."""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        return "".join(response_text(r) for r in resp)
    if isinstance(resp, dict):
        keys = ("stdout", "stderr", "output", "content", "text", "result", "filenames")
        return "".join(response_text(resp[k]) for k in keys if k in resp)
    return str(resp)


def written_lines(tool, inp):
    """New lines main wrote in this call, or None when the call writes no file."""
    if tool in ("Edit", "Write", "NotebookEdit"):
        text = inp.get("new_string") or inp.get("content") or inp.get("new_source") or ""
        return str(text).count("\n") + 1
    if tool == "MultiEdit":
        return sum(str(e.get("new_string") or "").count("\n") + 1 for e in inp.get("edits") or [] if isinstance(e, dict))
    if tool in ("Bash", "PowerShell") and SCRIPT_WRITE.search(str(inp.get("command", ""))):
        return 0
    return None


def edit_state(update):
    """Read-modify-write of the per-session write streaks; returns update's result."""
    path = LOG.with_name("subagents-edit-state.json")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    result = update(state)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(state) > 50:  # keep the newest sessions only
            state = dict(sorted(state.items(), key=lambda kv: kv[1].get("t", 0))[-50:])
        path.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass
    return result


def count_write(ev, tool, lines):
    """Advance this session's streak; a reminder text when the streak or the size calls for the editor."""
    session = str(ev.get("session_id") or "default")

    def bump(state):
        s = state.get(session) if isinstance(state.get(session), dict) else {}
        s = {"writes": int(s.get("writes", 0)) + 1, "t": time.time()}
        state[session] = s
        return s["writes"]

    writes = edit_state(bump)
    big = lines >= EDIT_LINES
    if not big and writes % EDIT_STREAK:
        return None
    log({"phase": "post", "tool": tool, "writes": writes, "lines": lines, "action": "remind-editor"})
    what = f"{lines} new lines in one call" if big else f"{writes} file writes by main since the last subagents:editor call"
    return (f"That was {what}. If the change is already decided (tests from given cases, removing debug code, the "
            "same patch in several files, copying files into place), hand the remaining writes to subagents:editor "
            "with the exact change and review its report. Keep writing yourself only while you are still working "
            "out what the fix is, or for a few lines in a file you have open.")


def reset_writes(ev):
    session = str(ev.get("session_id") or "default")
    edit_state(lambda state: state.pop(session, None))


def post(ev, inp, cwd):
    tool = ev.get("tool_name", "")
    notes = []
    lines = written_lines(tool, inp)
    if lines is not None:
        note = count_write(ev, tool, lines)
        if note:
            notes.append(note)
    kb = len(response_text(ev.get("tool_response")).encode("utf-8", "ignore")) / 1024
    if kb >= OUT_KB and tool not in WRITE_TOOLS:
        log({"phase": "post", "tool": tool, "kb": round(kb, 1), "action": "remind",
             "cmd": str(inp.get("command") or inp.get("pattern") or "")[:200]})
        notes.append(f"That {tool} call produced about {kb:.0f} KB of output. Whatever part of it was shown stays in "
                     "the main context. For similar long-output work, use subagents:runner for commands or "
                     "subagents:reader for searches, and get back only the result.")
    if notes and MODE != "observe":
        emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": " ".join(notes)}})


def main():
    try:
        ev = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
        if not isinstance(ev, dict) or ev.get("agent_id"):
            return  # bad input, or a subagent's own call
        inp = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
        (post if len(sys.argv) > 1 and sys.argv[1] == "post" else pre)(ev, inp, ev.get("cwd") or "")
    except Exception:
        pass  # fail open


MODE = os.environ.get("SUBAGENTS_GATE", "on")
INHERITING_TYPES = ("Explore", "Plan")
READ_KB = env_kb("SUBAGENTS_READ_KB", 40)
OUT_KB = env_kb("SUBAGENTS_OUT_KB", 20)
EDIT_STREAK = max(1, int(env_kb("SUBAGENTS_EDIT_STREAK", 3)))
EDIT_LINES = env_kb("SUBAGENTS_EDIT_LINES", 40)

if __name__ == "__main__":
    if MODE != "off":
        main()
    sys.exit(0)
