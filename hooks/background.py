"""Default main-agent shell commands (Bash, PowerShell) to the background (0.12.0).

A long command in the foreground blocks the whole main conversation; in the background the agent keeps working and is
re-invoked when it exits. Only an absent (or non-boolean) run_in_background is defaulted to true: an explicit boolean
(true or false) is the agent's own choice and is left alone, so a quick read can run in the foreground and return its
output at once.
Subagent calls (input carries agent_id) are left alone: a subagent can end before its background output arrives.
No permissionDecision is returned, so the normal permission flow still applies.
SUBAGENTS_BACKGROUND=off disables it. Any error lets the call through unchanged; the exit code is always 0.
"""
import json
import os
import sys


def main():
    if os.environ.get("SUBAGENTS_BACKGROUND", "").lower() == "off":
        return
    try:
        ev = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError:
        return
    if not isinstance(ev, dict) or ev.get("agent_id") or ev.get("tool_name") not in ("Bash", "PowerShell"):
        return
    tool_input = ev.get("tool_input")
    if not isinstance(tool_input, dict) or isinstance(tool_input.get("run_in_background"), bool):
        return
    out = {"hookEventName": "PreToolUse", "updatedInput": {**tool_input, "run_in_background": True}}
    sys.stdout.write(json.dumps({"hookSpecificOutput": out}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
