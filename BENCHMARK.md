# Sub-agent model benchmark: results (2026-10-11)

## Question
When the main agent (Opus) keeps all judgement and delegates only reading and writing, which model should do the
delegated work: haiku-medium, sonnet-medium, or the main agent itself?

## Setup
- **Runs:** each run is a separate headless `claude -p` session (`--no-session-persistence`) in a fresh copy of the
  task. Runs execute 6 at a time, in random order. The script is `run.py`.
- **Tasks:** a 10-task ladder (`repo_tasks.py`) on a frozen snapshot of the glance repo (`snapshot/`). There are five
  reading tasks and five writing tasks, at five levels:
  1. find something
  2. collect it completely
  3. follow the logic
  4. work across files and infer
  5. trace through the whole project
- **Write scoring:** hidden tests, plus a check that no file outside the allowed ones changed.
- **Read scoring:** the answer must be one JSON object, and each fact in it is compared to the ground truth.
- **Repetitions:** 2 runs per (task, model) cell.
- **Cost columns:**
  - `raw$` is what was actually billed.
  - `cold$` adds back the prompt-cache reuse from other runs, so it is the cost with a cold cache.
  - Real costs fall between the two.
  - Both use API list prices, which are not the same as subscription usage drawdown.

## Result
Haiku-medium passed every task, including the level-5 whole-project tasks. Sonnet never beat Haiku on any task.

| Model | Tasks fully solved (of 10) | Cold cost per delegation | Time |
|---|---|---|---|
| haiku-medium | 10 (after fixing the scorer, see below) | **$0.011-0.017** | 11-40 s |
| sonnet-medium | 10 | $0.20-0.27 | 10-25 s |
| opus-medium | 9 (w3 scored 0.75 once) | $0.39-0.51 | 12-27 s |

Notes on the table:
- **Where the cost comes from:** almost all of it is a fixed per-session cost, about 36-50k tokens of system prompt and
  tools. Haiku costs about 1/18 of Sonnet and 1/37 of Opus.
- **Speed:** Haiku was slower only on w5 (40 s against 21-25 s).
- **The earlier toy tasks gave the same picture:** easy, medium and hard read and code tasks were all solved by every model.

## Scorer problems found (the models' answers were not wrong)
- **r1:** every model answered `3e-5` as a JSON number, which parses to `3e-05` and failed a string compare. Fixed:
  numbers now compare by value.
- **r4:**
  - **Sonnet:** answered `hash` as "blake2b (8-byte digest, ...)", correct but verbose, and the strict compare scored
    it 5/6. This is still strict and has not been fixed.
  - **Re-run with answers captured:** Haiku scored 6/6 on both runs.
- **Opus w3 0.75:** happened on one run. Answers and changed files were not stored for that row, so the cause is
  unknown. `run.py` now stores both.

## Implications
1. **For delegated reading and writing, use Haiku-medium.** No task here needed Sonnet. Use Sonnet only as an
   escalation when the main agent's review of Haiku's result finds a problem.
2. **The real choice is "delegate to Haiku" or "do it in the main agent".** It depends on the main context's token
   cost, not on Haiku's quality:
   - **Delegation pays when the result is much smaller than the source:** big logs, multi-file surveys, mechanical
     multi-site edits.
   - **The main agent should do it directly** for a small file it would read anyway, or a few-line edit.
3. **The Jev/Glance router test cannot discriminate with these tasks.** If the gold label is "the cheapest model that
   passed", every label is haiku, and a router that always says haiku would score 100%. Skip it until a task exists
   where Haiku fails. A size-based rule (delegate when the source is much larger than the expected answer) needs no
   learned router.

## Caveats
- n=2 per cell.
- Headless `-p` sessions do not include the Agent tool's sub-agent instructions or the parent's CLAUDE.md. Real
  delegation prompts are longer, so the absolute Haiku cost will be somewhat higher. The ranking should not change.
- The tasks are specified precisely, as the main agent would write them. With vague instructions the results may differ.
