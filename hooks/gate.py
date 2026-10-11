"""Delegation gate for the main agent. Calls made by subagents (input carries agent_id) are never gated.

pre (PreToolUse):
- Agent/Task calls that would run on the main model (no subagents:* type, no explicit model) get a reminder
  to use the Haiku agents for mechanical work. Never denied.
- WebFetch, and the browser page readers get_page_text / read_page in every browser variant, are always denied.
  They are bulk by nature, so they go to subagents:web or subagents:browser.
- A Read whose returned bytes reach SUBAGENTS_READ_KB (default 40 KB) is denied. The bytes are counted for the
  requested lines (offset, then limit with a default of 2000), with each line capped at Read's 2000-character cut.
  Counting stops at the threshold. Images and PDFs are exempt, because main may need to see them.
- The plain shell dump `cat/type/Get-Content/gc FILE...` of a big file is denied in any `;`/`&&`/`||` segment,
  unless the segment is piped into head/tail/wc/Select-Object -First/-Last. Other dump forms get the post reminder.
post (PostToolUse on Bash, PowerShell, Grep):
- Output text of SUBAGENTS_OUT_KB or more (default 20 KB) adds a reminder. It cannot block, because the call already ran.

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
        if st.startswith("subagents:") or model:
            return  # already a Haiku agent, or the model was chosen on purpose (e.g. a review)
        log({"phase": "pre", "tool": tool, "subagent_type": st or "general-purpose", "action": "advise"})
        if MODE != "observe":
            emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
                  f"This delegates to '{st or 'general-purpose'}', which runs on the main model. If the task is mechanical "
                  "(reading/summarising files or logs, applying decided edits, running commands, web lookups, page "
                  "checks), use subagents:reader / editor / runner / web / browser instead - Haiku, about 1/25 the cost. "
                  "Keep this agent only for work that needs judgement."}})
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


def post(ev, inp, cwd):
    tool = ev.get("tool_name", "")
    kb = len(response_text(ev.get("tool_response")).encode("utf-8", "ignore")) / 1024
    if kb < OUT_KB:
        return
    log({"phase": "post", "tool": tool, "kb": round(kb, 1), "action": "remind",
         "cmd": str(inp.get("command") or inp.get("pattern") or "")[:200]})
    if MODE == "observe":
        return
    emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
          f"That {tool} call produced about {kb:.0f} KB of output. Whatever part of it was shown stays in the main "
          "context. For similar long-output work, use subagents:runner for commands or subagents:reader for searches, "
          "and get back only the result."}})


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
READ_KB = env_kb("SUBAGENTS_READ_KB", 40)
OUT_KB = env_kb("SUBAGENTS_OUT_KB", 20)

if __name__ == "__main__":
    if MODE != "off":
        main()
    sys.exit(0)
