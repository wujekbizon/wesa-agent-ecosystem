# WESA Agent Daemon — Technical Reference

**File:** `daemon.py`  
**Version:** v0.7  
**Module:** WESA Agent Ecosystem Core Runtime

---

## Overview

The Agent Daemon is the central orchestrator of the WESA Agent Ecosystem. It runs continuously in the background, polling a `tasks/` directory for new JSON task files and dispatching them to the appropriate AI agents via the `Router`. Results, logs, and a human-readable briefing are written to the `logs/` directory.

Run the daemon once from the `agent-ecosystem/` directory and leave it running. Any collaborator or automation can submit work by dropping a `.json` file into `tasks/`.

---

## Quick Start

**Start the daemon:**
```bash
cd agent-ecosystem/
python daemon.py
```

**Submit a task:**
```bash
# Drop a JSON file into tasks/
echo '{ "id": "task-001", "type": "code", "prompt": "Add docstrings to utils.py" }' > tasks/task-001.json
```

**Stop the daemon:**
```bash
# Ctrl+C in terminal, or:
kill $(cat logs/daemon.pid)
```

---

## Directory Structure

The daemon expects to be run from `agent-ecosystem/` (the repo root). All directories are created automatically on startup.

| Path | Purpose |
|------|---------|
| `tasks/` | Drop `.json` task files here to submit work |
| `logs/` | Root log directory |
| `logs/designs/` | Reports for `design`, `frontend`, `review`, `skill_update`, `skill_merge` tasks |
| `logs/research/` | Reports for `research` and `analyze` tasks |
| `logs/dev/` | Reports for `code`, `write`, `test`, `git`, `assets`, `general` tasks |
| `logs/tasks.db` | SQLite database — canonical task state |
| `logs/staging.db` | SQLite staging queue for Research Center briefs |
| `logs/AXEL_BRIEFING.md` | Auto-generated status briefing, rebuilt after every task |
| `logs/daemon.pid` | PID of the running daemon process |
| `logs/rc_schedule.json` | Research Center auto-scheduler state (round-robin index + last triggered time) |
| `logs/rc_enabled.flag` | If this file exists, the RC auto-scheduler is active |

---

## Task File Format

Task files are JSON objects placed in `tasks/`. The daemon picks them up within `POLL_INTERVAL` seconds (default: 3s).

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique task identifier. Used as the filename prefix for reports. |
| `type` | Yes | Task type — controls agent routing and log subfolder. See [Task Types](#task-types). |
| `prompt` | Yes | Instruction text sent to the agent. |
| `output_file` | No | Relative path from workspace root. If set, the agent's result is written here. |
| `model` | No | Override the default model for this task. |
| `status` | Auto | Managed by the daemon: `pending` → `in_progress` → `done` / `failed`. |

**Example task file:**
```json
{
  "id": "task-42",
  "type": "code",
  "prompt": "Refactor the auth module to use async/await",
  "output_file": "src/auth.py"
}
```

### Task Types

The `type` field determines which agent handles the task and where its report is stored.

| Type | Log Directory | Notes |
|------|--------------|-------|
| `design` | `logs/designs/` | UI/UX design tasks |
| `frontend` | `logs/designs/` | Frontend implementation |
| `review` | `logs/designs/` | Design review / QA |
| `code_review` | `logs/dev/` | Code review |
| `skill_update` | `logs/designs/` | Agent skill update |
| `skill_merge` | `logs/designs/` | Agent skill merge |
| `research` | `logs/research/` | Research tasks |
| `analyze` | `logs/research/` | Analysis tasks |
| `code` | `logs/dev/` | Code generation / editing |
| `write` | `logs/dev/` | Writing tasks |
| `test` | `logs/dev/` | Test generation / running |
| `assets` | `logs/dev/` | Asset generation |
| `git` | `logs/dev/` | Git operations — auto-enriched with live diff; commands auto-executed |
| `general` | `logs/dev/` | Catch-all for uncategorized tasks |
| `coordinate` | *(consumed)* | Pipeline control — not sent to an agent. See [Pipelines](#pipelines). |

---

## Main Loop

The daemon's `run()` function executes the following steps every `POLL_INTERVAL` seconds (default: 3s):

1. `sync_json_to_db()` — imports any new `.json` files from `tasks/` into SQLite.
2. `auto_run_research_center()` — fires the next RC topic if the scheduler is active and the interval has elapsed.
3. For each `.json` file in `tasks/` (sorted alphabetically), call `process_task()`.
4. Sleep `POLL_INTERVAL` seconds and repeat.

The loop exits cleanly on `KeyboardInterrupt` (Ctrl+C) or `SIGTERM`.

---

## Task Processing

`process_task()` handles the full lifecycle of a single task.

### Lifecycle

1. **Skip** if the task's status is not `pending`.
2. **Pipeline trigger** — `coordinate` tasks with a `START_PIPELINE:` prompt are consumed immediately: the pipeline is started and the task is marked `done` without being sent to an agent.
3. **Git enrichment** — `git` tasks have live repository context injected into the prompt automatically.
4. Status updated to `in_progress` in both the `.json` file and SQLite.
5. **Router** dispatches the task to the appropriate agent.
6. **On success:** result saved, report written, `output_file` written if specified, briefing updated, pipeline controller notified.
7. **On failure:** error recorded, task marked `failed`, briefing updated, failure notification sent.

### Retry Logic

Transient API errors are retried automatically with exponential backoff before marking a task as failed.

**Retried error signals:** HTTP `503`, `429`, `UNAVAILABLE`, `deadline expired`, `high demand`, `resource exhausted`, `service unavailable`

| Attempt | Delay |
|---------|-------|
| 1st retry | 30 seconds |
| 2nd retry | 60 seconds |
| 3rd retry | 120 seconds |
| After 3rd failure | Task marked `failed` |

### Task Reports

After every task, a Markdown report is written to the appropriate log subdirectory as `<task-id>_report.md`. It contains the task ID, type, status, model used, timestamps, the original prompt, and the full result or error message.

---

## Git Integration

Tasks with `type: git` receive special treatment.

### Prompt Enrichment

Before routing, `enrich_git_prompt()` automatically injects live repository context into the prompt:

- `git status --short`
- `git log --oneline -5`
- `git diff HEAD` (capped at 400 lines)

The submitter only needs to write a brief note or leave the prompt empty — the daemon fills in the diff.

### Auto-Execution

After the agent responds, `execute_git_block()` parses the output for `git add` and `git commit -m` commands (inside or outside markdown code fences) and executes them against the repo directory. The execution summary is appended to the task result.

---

## Pipelines

The `PipelineController` orchestrates multi-step workflows defined as YAML files in `agents/pipelines/`.

### Triggering a Pipeline Manually

Drop a `coordinate` task with this prompt format:

```
START_PIPELINE:<pipeline-name> TOPIC:<topic text>
```

**Example:**
```json
{
  "id": "run-001",
  "type": "coordinate",
  "prompt": "START_PIPELINE:research-center TOPIC:Accessibility in mobile UI"
}
```

### Research Center Auto-Scheduler

If `logs/rc_enabled.flag` exists, the daemon automatically triggers the `research-center` pipeline on a round-robin schedule across topics defined in the pipeline YAML.

| Action | Command |
|--------|---------|
| Enable scheduler | `touch logs/rc_enabled.flag` |
| Disable scheduler | `rm logs/rc_enabled.flag` |
| Change interval | Set `RC_INTERVAL_HOURS` env var before starting the daemon |

The scheduler skips triggering if a `research-center` pipeline run is currently active. A running pipeline is considered stale after `STALE_PIPELINE_HOURS` (default: 2) and is auto-closed to unblock the scheduler.

### Pipeline Notifications

When a pipeline finishes, a notification block is appended to `AXEL_BRIEFING.md`. Three outcomes:

| Stop Reason | Meaning |
|-------------|---------|
| `completed` | Design approved and skills merged into `ux_agent.yaml`. Review the merged rules for quality. |
| `max_iterations_reached` | Loop exhausted all iterations without reaching the approval threshold. Review `logs/designs/` for findings. |
| Other | Check the pipeline state file for details. |

---

## Axel Briefing

`logs/AXEL_BRIEFING.md` is a human-readable status document rebuilt from SQLite after every task completion.

**Contents:**
- Task summary counts (done / failed / pending / in_progress)
- **Needs Attention** — all failed tasks with truncated error messages
- **Pending Tasks** — tasks waiting to run
- **Recently Completed** — last 10 done tasks with model and timestamp
- Pipeline notification blocks appended after each pipeline completes

Read this file at the start of any session to restore full context on the ecosystem's state.

---

## Configuration Reference

| Constant / Env Var | Default | Description |
|--------------------|---------|-------------|
| `POLL_INTERVAL` | `3` | Seconds between each scan of the `tasks/` directory |
| `RC_INTERVAL_HOURS` | `4` | Minimum hours between Research Center auto-triggers (`RC_INTERVAL_HOURS` env var) |
| `STALE_PIPELINE_HOURS` | `2` | Hours after which a running pipeline is considered stale and auto-closed |
| `RETRY_DELAYS` | `[30, 60, 120]` | Seconds to wait before each retry attempt on transient errors |
| `TASKS_DIR` | `tasks/` | Directory watched for incoming task files |
| `LOGS_DIR` | `logs/` | Root directory for all output files |
| `BRIEFING_FILE` | `logs/AXEL_BRIEFING.md` | Path to the Axel briefing file |
| `RC_ENABLED_FILE` | `logs/rc_enabled.flag` | Presence of this file enables the RC scheduler |
| `RC_SCHEDULE_FILE` | `logs/rc_schedule.json` | Persisted RC scheduler state |

---

## Key Dependencies

| Import | Role |
|--------|------|
| `src.task.Task` | Task data model and file I/O |
| `src.task.TaskStatus` | Enum: `PENDING`, `IN_PROGRESS`, `DONE`, `FAILED` |
| `src.router.Router` | Dispatches tasks to the correct agent |
| `src.notifier` | Sends started / done / failed notifications |
| `src.task_queue.TaskQueue` | SQLite-backed task state store |
| `src.pipeline_controller.PipelineController` | Manages multi-step pipeline execution |
| `src.staging_queue.StagingQueue` | Staging buffer for Research Center briefs |

---

## Changelog

| Version | Key Changes |
|---------|-------------|
| v0.5 | SQLite queue integrated (`logs/tasks.db`); `update_briefing()` reads from SQLite; output file paths resolved relative to workspace root |
| v0.6 | `PipelineController` wired in; `StagingQueue` wired in; `START_PIPELINE:` prompt trigger added |
| v0.7 | Automatic retry with exponential backoff for transient API errors (3 retries: 30s → 60s → 120s) |