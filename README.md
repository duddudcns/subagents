# haiku-delegate

The main agent (Opus) keeps all thinking: review, decisions, analysis. Mechanical work goes to four Haiku 5.5
(medium effort) subagents. They skip CLAUDE.md and get only the tools they need, which keeps the startup cost low.

| Agent | Use for | Tools |
|---|---|---|
| `haiku-reader` | Getting facts or a short summary from big files, logs, many files, or repo-wide search | Read, Grep, Glob, Bash (read-only) |
| `haiku-editor` | Edits the main agent already decided: renames, repeated changes, a precise spec | Read, Edit, Write, Grep, Glob |
| `haiku-runner` | Running given commands with long output: tests, builds, log queries, ssh status | Bash, PowerShell, Read, Grep, Glob |
| `haiku-web` | Web lookups and research, with sources | WebSearch, WebFetch |

Each agent answers in a fixed short format:
- It gives `path:line` for every fact.
- It writes "not found" when something is missing and never guesses.
- It lists tricky points (rounding, null vs empty, boundary values, later corrections) under "Uncertain".

## Install

```
/plugin marketplace add duddudcns/claude-plugins
/plugin install haiku-delegate@duddudcns
```

## Recommended rule (add to your CLAUDE.md)

> Judgement stays in main. Mechanical reading, edits, command runs and web lookups go to the haiku-delegate agents.
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
