---
name: tiered-loop
description: "In-session orchestrator loop where the main model (Opus) only plans and verifies while cheaper sub-agents do the work. Routes each subtask to an ECC specialist agent or a plain haiku/sonnet agent, verifies against acceptance criteria, and re-loops failures with concrete feedback (max 2 rounds, escalating haiku -> sonnet -> self). Use for tasks made of many similar parts (summarize many files, translate many pages, migrate many call sites). Skip for one-off small fixes, a single hard problem, or strictly sequential steps."
metadata:
  origin: ECC
---

# Tiered Loop: Opus Plans and Verifies, Cheaper Models Work

The main session acts as orchestrator. It never does bulk work itself. It
decomposes, delegates to cheaper tiers, verifies each result against explicit
acceptance criteria, and sends failures back with feedback.

This is the in-session, interactive counterpart to the mechanism-layer loop
skills (`autonomous-loops`, `continuous-agent-loop`) and uses the same tier
heuristic as `/model-route`.

## When to Use

- The task consists of many similar parts plus a little real judgment.
- The bulk work is parallelizable and each part can be checked on its own.
- Examples: summarize 40 interview notes and rank the top problems; translate
  every page of a site; replace every `sendMail()` call with `sendEmail()` and
  get tests green.

Skip when:

- The task is small (a typo, one rename). Do it directly, ideally on a cheap
  model (`/model haiku` or `/model sonnet`).
- It is one hard problem (tricky bug, strategy question). The main model
  should handle it alone.
- Each step needs the full output of the previous one. Sub-agent hand-offs
  lose more than they save.
- The data is sensitive (customer, financial, health). Clarify what may be
  read first.

Every sub-agent starts with an empty context and has to read in. On small
tasks that overhead costs more than the delegation saves.

## How It Works

### 1. Plan

Read the task and only as much of the project as the plan needs. Split it into
clearly bounded subtasks. For each one define:

- Goal in one sentence
- Acceptance criteria: how you will check it is done and correct. Prefer
  machine-checkable criteria (a test passes, a grep returns zero hits, every
  input file has an output file).
- Executor: an ECC agent, a plain `haiku`/`sonnet` agent, or `self`

Show the plan as a short table, then start immediately.

### 2. Route

Pick the cheapest executor that can reliably pass the criteria. Prefer an ECC
specialist when one matches, because its system prompt already carries the
domain checklist.

| Work type | Tier | ECC agent (if installed) |
|-----------|------|--------------------------|
| Find, read, extract, summarize, rename, format | haiku | plain agent with `model: "haiku"` |
| Docs and codemaps | haiku | `doc-updater` |
| Library/API usage lookup | haiku | `docs-lookup` |
| Comment accuracy | haiku | `comment-analyzer` |
| Write code, tests, drafts; multi-file edits | sonnet | plain agent with `model: "sonnet"` |
| Test-first implementation | sonnet | `tdd-guide` |
| Build or type errors | sonnet | `build-error-resolver` or `<lang>-build-resolver` |
| Dead code, consolidation | sonnet | `refactor-cleaner` |
| Code review (sub-step quality gate) | sonnet | `code-reviewer` or `<lang>-reviewer` |
| Security-sensitive changes | sonnet | `security-reviewer` |
| Codebase mapping before a change | sonnet | `code-explorer` |
| Architecture, hard trade-offs, final verification | self | none |

Rules:

- When in doubt, use sonnet, not self.
- Do not delegate to agents defined with `model: opus` (`planner`,
  `architect`, `spec-miner`, `healthcare-reviewer`) inside this loop. That
  work is the orchestrator's job, and delegating it doubles the Opus cost.
- With the plugin install, agent names are namespaced (`ecc:tdd-guide`).
  Use whichever form is registered in the session.
- An explicit `model` parameter on the Agent tool overrides the agent's
  frontmatter model. Use it to run a specialist one tier lower when the
  subtask is mechanical.

### 3. Delegate

Start every subtask through the Agent tool (formerly Task tool). For plain
agents, set `model` explicitly to `"haiku"` or `"sonnet"`.

Sub-agents do not see the conversation. Every brief must stand alone:

```text
GOAL: <one sentence>
FILES: <exact paths or glob>
ACCEPTANCE CRITERIA:
- <criterion 1>
- <criterion 2>
CONSTRAINTS: do not delete files, do not push, do not touch <paths>
RETURN (max ~15 lines):
- CHANGED: <file:line list or "none">
- RESULT: <summary or output location>
- CRITERIA: <each criterion -> met / not met / unsure>
- OPEN: <anything unresolved>
```

Start independent subtasks in parallel, in one message.

Batch mechanical work. Give one haiku agent 10 files, not ten agents one file
each. Too many tiny agents is the most common way this loop burns its savings.

### 4. Verify

Check each result against its acceptance criteria. Read the changed lines or
run the tests (`verification-loop` covers build/type/lint/test gates) instead
of redoing the work. Do not trust the sub-agent's own CRITERIA line; it is a
pointer for where to look, not proof.

Pass: tick it off. Fail: write down concretely what is missing or wrong, with
file and line.

### 5. Loop

Send the feedback to a new sub-agent, same tier or one higher
(haiku -> sonnet). Include the previous result and the exact failure, not just
"try again". Maximum 2 rounds per subtask. If it still fails, take over
yourself or ask the user.

If the same kind of subtask fails on haiku twice, route all remaining subtasks
of that kind to sonnet for the rest of the run.

### 6. Close

When everything passes, report:

- what is done
- a table: Subtask | Executor | Model | Rounds | Result
- what was deliberately not done, and why

Rows with 2 rounds deserve a human look.

### Guardrails

- Do not delete, push, or publish anything without the user's OK.
- Do not delegate anything shorter than the brief needed to delegate it.
- Stop and re-plan if acceptance criteria turn out to be vague. A loop that
  keeps circling almost always has fuzzy criteria.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| The main model does everything itself | Confirm you are in Claude Code (sub-agents are unavailable in plain chat). Then state: "Use the Agent tool with model haiku or sonnet for step 3." |
| All sub-agents still run on Opus | Check for an environment variable that forces one model for all sub-agents (for example `CLAUDE_CODE_SUBAGENT_MODEL`). |
| Haiku results are too weak | The subtask was not mechanical enough. Route that kind to sonnet. |
| Usage limit still drains fast | Fewer, larger subtasks; shorter briefs. |
| The loop circles | Sharpen the acceptance criteria before continuing. |

## Examples

### Customer feedback

```text
/tiered-loop The folder interviews/ holds 40 call notes as .txt. Summarize
each, find the 5 most common customer problems, and write a report with
quotes and a recommendation for what to tackle first.
```

Plan: 4 haiku agents summarize 10 files each into `summaries/`; 1 sonnet agent
clusters problems and pulls quotes; the orchestrator checks every quote
against its source file and writes the recommendation.

### Code migration

```text
/tiered-loop Replace every call to sendMail() with sendEmail() and make all
tests pass.
```

Plan: 1 haiku agent lists call sites (criterion: list matches
`grep -rn "sendMail("`); sonnet agents migrate batches of files
(criterion: zero remaining hits in their batch); `build-error-resolver` fixes
fallout; the orchestrator inspects only call sites whose tests stay red, then
runs the full suite.

## Related

- `/tiered-loop` command: entry point for this skill
- `/model-route`: single-task tier recommendation
- `verification-loop`: gates used in step 4
- `loop-design-check`: review a loop design for spinning or verifier gaming
- `autonomous-loops`, `continuous-agent-loop`: unattended, multi-session loops
