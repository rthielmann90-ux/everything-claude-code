---
description: Run a task as a tiered orchestrator loop. The main model plans and verifies, haiku/sonnet sub-agents and ECC specialists do the work, failures re-loop with feedback (max 2 rounds).
---

# Tiered Loop Command

Run the task in `$ARGUMENTS` using the `tiered-loop` skill.

## Usage

`/tiered-loop <task description>`

## Flow

1. Decide whether the loop is worth it (see the skill's skip list). If not,
   say so in one sentence and do the task directly.
2. Plan: subtasks with goal, acceptance criteria, executor. Show as a table.
3. Route each subtask to the cheapest capable executor (ECC specialist or plain
   `haiku`/`sonnet` agent with an explicit `model` parameter).
4. Delegate with self-contained briefs; run independent subtasks in parallel.
5. Verify every result against its criteria; re-loop failures at most twice,
   escalating haiku -> sonnet -> self.
6. Close with: done items, table `Subtask | Executor | Model | Rounds | Result`,
   and what was deliberately skipped.

## Guardrails

- No deleting, pushing, or publishing without the user's OK.
- Do not delegate anything shorter than its brief.

## Arguments

$ARGUMENTS:
- `<task description>` required free-text task
