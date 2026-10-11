"""Delegation gate for the main agent. Calls made by subagents (input carries agent_id) are never gated.

pre (PreToolUse):
- WebFetch, and the browser page readers get_page_text / read_page in every browser variant, are always denied.
  They are bulk by nature, so the call goes to subagents:web or subagents:browser.
- A Read whose expected size is SUBAGENTS_READ_KB or more (default 40 KB) is denied. The size is estimated from the
  lines it would return (limit, default 2000) times the file's average line size, so a huge limit is no bypass.
  Images and PDFs are exempt, because main may need to see them.
- Bash/PowerShell that just dumps a big file (cat/type/Get-Content/gc FILE with no head/tail/select) is denied the same way.
post (PostToolUse on Bash, PowerShell, Grep):
- Output text of SUBAGENTS_OUT_KB or more (default 20 KB) adds a reminder. It cannot block, because the output already ran.

Every decision is appended to ~/.claude/subagents-gate.jsonl. Secrets are masked and the log rotates at 5 MB.
SUBAGENTS_GATE=off disables the gate; SUBAGENTS_GATE=observe logs decisions without acting on them.
Any error lets the call through: the gate fails open and always exits 0.
"""
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

LOG = Path.home() / ".claude" / "subagents-gate.jsonl"
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf")
READ_DEFAULT_LINES = 2000
DELEGATE_ALWAYS = re.compile(r"^(WebFetch|mcp__.*(Claude_Browser|claude-in-chrome).*__(get_page_text|read_page))$")
DUMP_CMD = re.compile(r"^\s*(cat|type|get-content|gc)\s+(.+?)\s*$", re.I)
PIPE_LIMITERS = re.compile(r"\|\s*(head|tail|select|select-object|grep|rg|findstr|wc|sed|awk)\b|-(totalcount|tail|head)\b", re.I)
SECRET = re.compile(r"(?i)(authorization|bearer|api[_-]?key|token|password|secret)([\"'=:\s]+)(?:(?:bearer|basic)\s+)?[^\s\"']+")


def env_kb(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def log(rec):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > 5_000_000:
            LOG.replace(LOG.with_suffix(".jsonl.1"))
        line = json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": MODE, **rec}, ensure_ascii=False)
        with LOG.open("a", encoding="utf-8", errors="replace") as f:
            f.write(SECRET.sub(r"\1\2***", line) + "\n")
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


def read_estimate_kb(path, limit):
    """KB a Read would return: the whole file if it has <= limit lines, else limit * average line size."""
    size = path.stat().st_size
    if size < 1024 * READ_KB:  # cannot exceed the threshold
        return size / 1024
    with path.open("rb") as f:
        lines = sum(1 for _ in f) or 1
    return min(size, size / lines * limit) / 1024


def pre(ev, inp, cwd):
    tool = ev.get("tool_name", "")
    if DELEGATE_ALWAYS.match(tool):
        agent = "subagents:web" if tool == "WebFetch" else "subagents:browser"
        log({"phase": "pre", "tool": tool, "action": "deny", "to": agent})
        deny(f"{tool} returns bulk page content that would stay in the main context. Delegate it to the {agent} agent "
             "with the exact question and what counts as an answer, then review its findings yourself. For a small "
             "check on a page you can also use the browser's find tool.")
        return
    if tool == "Read":
        path = resolve(inp.get("file_path", ""), cwd)
        if path.suffix.lower() in SKIP_EXT or not path.is_file():
            return
        limit = inp.get("limit")
        limit = int(limit) if isinstance(limit, (int, float)) and limit > 0 else READ_DEFAULT_LINES
        kb = read_estimate_kb(path, limit)
        if kb < READ_KB:
            return
        log({"phase": "pre", "tool": tool, "path": str(path), "kb": round(kb, 1), "limit": limit, "action": "deny"})
        deny(f"This Read would return about {kb:.0f} KB (about {kb * 256:.0f} tokens) of {path.name}. It would stay in the "
             "main context and be re-read on every later call. Delegate it to the subagents:reader agent with the exact "
             "question. If you need specific lines for your own judgement, Read a smaller range: use offset/limit with "
             f"fewer lines, below about {READ_KB:.0f} KB.")
        return
    if tool in ("Bash", "PowerShell"):
        cmd = str(inp.get("command", ""))
        m = DUMP_CMD.match(cmd)
        if not m or PIPE_LIMITERS.search(cmd) or re.search(r"[;&|<>]", m.group(2)):
            return
        try:
            args = shlex.split(m.group(2), posix=os.name != "nt")
        except ValueError:
            return
        files = [resolve(a, cwd) for a in args if not a.startswith("-")]
        kb = sum(f.stat().st_size for f in files if f.is_file()) / 1024
        if kb < READ_KB:
            return
        log({"phase": "pre", "tool": tool, "cmd": cmd[:200], "kb": round(kb, 1), "action": "deny"})
        deny(f"This command dumps about {kb:.0f} KB of file content into the main context. Delegate it to the "
             "subagents:reader agent with the exact question. If you need specific lines, use Read with offset/limit.")


def response_text(resp):
    """Only the text that reaches the model: stdout/stderr/output/content fields, not metadata."""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        return "".join(response_text(r) for r in resp)
    if isinstance(resp, dict):
        keys = [k for k in ("stdout", "stderr", "output", "content", "text", "result") if k in resp]
        if keys:
            return "".join(response_text(resp[k]) for k in keys)
        return json.dumps(resp, ensure_ascii=False)
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
          f"That {tool} output was about {kb:.0f} KB of text in the main context. For similar long-output work, use "
          "subagents:runner for commands or subagents:reader for searches, and get back only the result."}})


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
