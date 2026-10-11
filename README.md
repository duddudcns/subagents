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

- **Bulk page reads are denied:** WebFetch, and `get_page_text` / `read_page` in the built-in browser, Chrome and
  remote-device variants. The deny message points to `subagents:web` or `subagents:browser`. WebSearch is allowed,
  because it returns short snippets.
- **Big reads are denied:** a Read whose requested lines (offset, then limit, default 2000) add up to about 40 KB or
  more, with each line capped like Read caps it. A huge `limit` is no bypass, and a tail read with a large `offset`
  passes when the remaining lines are small. Counting stops at the threshold, so huge files stay fast.
  - A smaller ranged Read for main's own judgement still passes.
  - Images and PDFs are exempt. Notebooks are gated.
- **The common shell dump is denied:** `cat`, `type`, `Get-Content` or `gc` of a file of 40 KB or more. This applies
  in any `;`, `&&` or `||` segment, and globs are expanded. It does not apply when the output is piped into head,
  tail, wc or `Select-Object -First`/`-Last`. Other dump forms (`head -c`, `tail -n +1`, scripts) are not caught;
  they get the post reminder instead.
- **Agent calls get a reminder only:** an Agent call without a `subagents:*` type and without an explicit `model` (it runs on the main model or that agent's default model, with its full tool set) gets a reminder. For mechanical work, the reminder points to the Haiku agents; it never blocks the call.
- **Big outputs get a reminder only:** Bash, PowerShell or Grep output of 20 KB or more adds a reminder. Only the
  output text is counted, not metadata.
- **Logging:** decisions go to `~/.claude/subagents-gate.jsonl`. Secrets are masked before serialisation, and the
  file rotates at 5 MB.
- **Latency:** about 100 ms per hooked call on Windows, mostly Python startup (`-S -I`). A shell command pays this
  twice, once before and once after.
- **Settings:**
  - `SUBAGENTS_GATE=observe` logs decisions without acting on them. `SUBAGENTS_GATE=off` disables the gate.
  - `SUBAGENTS_READ_KB` and `SUBAGENTS_OUT_KB` set the thresholds. A bad value falls back to the default.
- **Failure behaviour:** any error lets the call through, and the exit code is always 0. The hook runs `python`, or
  `python3` when `python` is missing.

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
