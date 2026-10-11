# subagents

The main agent (Opus) keeps all thinking: review, decisions, analysis. Mechanical, token-heavy work goes to these
subagents. They currently run on Haiku 5.5 with medium effort; the model is set per agent in `agents/*.md`. They skip CLAUDE.md and get only the tools they need, which keeps the startup cost low.

| Agent | Use for | Tools |
|---|---|---|
| `reader` | Getting facts or a short summary from big files, logs, many files, or repo-wide search | Read, Grep, Glob, Bash (read-only) |
| `editor` | Edits the main agent already decided: renames, repeated changes, a precise spec | Read, Edit, Write, Grep, Glob |
| `runner` | Running given commands with long output: tests, builds, log queries, ssh status | Bash, PowerShell, Read, Grep, Glob |
| `web` | Web lookups and research, with sources | WebSearch, WebFetch |
| `browser` | Page checks: page text or structure, screenshots, console errors. Read-only, never submits | Claude Browser read tools |

Each agent answers in a fixed short format:
- It gives `path:line` for every fact.
- It writes "not found" when something is missing and never guesses.
- It lists tricky points (rounding, null vs empty, boundary values, later corrections) under "Uncertain".

## Gate hook (enforced, main agent only)

`hooks/gate.py` enforces delegation instead of relying on a rule. Calls made by subagents are never gated.

- **Web and browser page reads are denied** (WebFetch, WebSearch, browser `get_page_text`, `read_page`). The deny
  message points to `subagents:web` or `subagents:browser`.
- **A whole-file Read of a file of 40 KB or more is denied.** The deny message points to `subagents:reader`, or to a
  ranged Read with offset/limit when main needs the lines for its own judgement.
- **Bash, PowerShell or Grep output of 20 KB or more adds a reminder only.** The output has already entered context by
  then, so it cannot be blocked.
- **Logging:** every decision is written to `~/.claude/subagents-gate.jsonl`.
- **Controls:**
  - `SUBAGENTS_GATE=observe` logs decisions only. `SUBAGENTS_GATE=off` disables the gate.
  - The thresholds are set with `SUBAGENTS_READ_KB` and `SUBAGENTS_OUT_KB`.
- **Failures:** the hook fails open (it allows the call) on bad input or a missing file.

## Install

```
/plugin marketplace add duddudcns/claude-plugins
/plugin install subagents@duddudcns
```

## Recommended rule (add to your CLAUDE.md)

> Judgement stays in main. Mechanical reading, edits, command runs and web lookups go to the subagents agents (subagents:reader, :editor, :runner, :web, :browser).
> Main reviews every result and verifies flagged points. Main does it directly when the content is what main must
> judge, or for a few-line edit in a file it already has open. Escalate to Sonnet only after Haiku fails a
> well-specified task twice.

## Why
See [BENCHMARK.md](BENCHMARK.md), measured 2026-10-11:
- On a 10-task real-repo ladder up to whole-project tracing, Haiku-medium matched Sonnet and Opus at 1/18 to 1/37 of the
  cost per fresh agent.
- On a second PC, across 99 runs, Haiku scored 98-100%. Its rare misses were subtle semantics: rounding, and VB
  `Nothing` vs empty string.
- The main agent must review results for those cases.
