import json
import re
import yaml
import glob
from datetime import datetime
from pathlib import Path
from typing import Optional
from src.task import Task, TaskType, TaskStatus
from src.task_queue import TaskQueue


class PipelineController:
    def __init__(
        self,
        pipelines_dir: str,
        tasks_dir: str,
        workspace_root: str,
        staging_queue=None,   # optional StagingQueue instance
    ):
        self.pipelines_dir = Path(pipelines_dir)
        self.tasks_dir = Path(tasks_dir)
        self.running_dir = self.pipelines_dir / "running"
        self.running_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_root = Path(workspace_root)
        self.staging_queue = staging_queue

        self._pipelines: dict[str, dict] = {}
        self._load_pipelines()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _load_pipelines(self) -> None:
        count = 0
        for file_path in glob.glob(str(self.pipelines_dir / "*.yaml")):
            if self.running_dir in Path(file_path).parents:
                continue
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    pipeline_def = yaml.safe_load(f)
                if pipeline_def and isinstance(pipeline_def.get("stages"), list):
                    self._pipelines[pipeline_def["name"]] = pipeline_def
                    count += 1
            except (yaml.YAMLError, FileNotFoundError):
                continue
        print(f"[PipelineController] Loaded {count} pipeline(s) from {self.pipelines_dir}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_pipeline(self, pipeline_name: str, queue: TaskQueue, topic: str = "") -> str:
        if pipeline_name not in self._pipelines:
            raise ValueError(f"Pipeline '{pipeline_name}' not found.")

        pipeline_def = self._pipelines[pipeline_name]
        run_id = f"run-{datetime.now().strftime('%Y%m%d')}-{abs(hash(pipeline_name + topic)) % 100000:06x}"
        first_stage = pipeline_def["stages"][0]

        state = {
            "pipeline": pipeline_name,
            "run_id": run_id,
            "current_stage_index": 0,
            "iteration": 1,
            "current_task_id": "",
            "stage_outputs": {},
            "started_at": datetime.utcnow().isoformat(),
            "status": "running",
            "topic": topic,
        }

        state_file = self.running_dir / f"{run_id}.json"
        self._save_state(state_file, state)

        task_id = self._queue_stage(pipeline_name, first_stage, state, "", queue)
        state["current_task_id"] = task_id
        self._save_state(state_file, state)

        return run_id

    def on_task_complete(self, task_id: str, result: str, queue: TaskQueue) -> None:
        """Call this from daemon after every task completion."""
        for state_file in list(self.running_dir.glob("*.json")):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                continue

            if state.get("status") == "running" and state.get("current_task_id") == task_id:
                self._advance(state_file, state, result, queue)
                return

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _advance(self, state_file: Path, state: dict, result: str, queue: TaskQueue) -> None:
        pipeline_name = state["pipeline"]
        if pipeline_name not in self._pipelines:
            self._mark_done(state_file, state)
            return

        pipeline_def = self._pipelines[pipeline_name]
        stages = pipeline_def["stages"]
        current_idx = state["current_stage_index"]

        if current_idx >= len(stages):
            self._mark_done(state_file, state)
            return

        current_stage = stages[current_idx]
        stage_id = current_stage.get("id", f"stage_{current_idx}")

        # Store this stage's result
        state["stage_outputs"][stage_id] = result

        # Handle push_to_staging action before checking stop/loop
        if current_stage.get("stage_action") == "push_to_staging" and self.staging_queue:
            self.staging_queue.add_brief(
                topic=state.get("topic", pipeline_name),
                brief_content=result,
                raw_research=state["stage_outputs"].get(stages[0].get("id", "stage_0"), ""),
                source_pipeline=pipeline_name,
            )

        # Evaluate stop condition
        stop_cond_met = self._eval_stop_if(current_stage.get("stop_if", ""), result)

        loop_to_id: Optional[str] = current_stage.get("loop_to")
        max_iter: int = pipeline_def.get("max_iterations", 10)

        if loop_to_id and not stop_cond_met:
            # Loop: increment iteration, check cap, jump back
            state["iteration"] += 1
            if state["iteration"] > max_iter:
                self._mark_done(state_file, state)
                return

            target_idx = next(
                (i for i, s in enumerate(stages) if s.get("id") == loop_to_id), -1
            )
            if target_idx == -1:
                self._mark_done(state_file, state)
                return

            state["current_stage_index"] = target_idx
            self._save_state(state_file, state)
            prev = state["stage_outputs"].get(stage_id, "")
            task_id = self._queue_stage(pipeline_name, stages[target_idx], state, prev, queue)
            state["current_task_id"] = task_id
            self._save_state(state_file, state)
            return

        # Advance to next stage
        next_idx = current_idx + 1
        if next_idx >= len(stages):
            self._mark_done(state_file, state)
            return

        state["current_stage_index"] = next_idx
        self._save_state(state_file, state)
        prev = state["stage_outputs"].get(stage_id, "")
        task_id = self._queue_stage(pipeline_name, stages[next_idx], state, prev, queue)
        state["current_task_id"] = task_id
        self._save_state(state_file, state)

    def _eval_stop_if(self, stop_if: str, result: str) -> bool:
        """Evaluates stop_if expression like 'score >= 9' against result text."""
        if not stop_if:
            return False
        match = re.search(r"TOTAL SCORE:\s*(\d+)", result)
        if not match:
            return False
        score = int(match.group(1))
        parts = stop_if.split()
        if len(parts) < 3:
            return False
        try:
            threshold = int(parts[2])
        except ValueError:
            return False
        op = parts[1]
        return (
            (op == ">=" and score >= threshold) or
            (op == ">" and score > threshold) or
            (op == "<=" and score <= threshold) or
            (op == "<" and score < threshold) or
            (op == "==" and score == threshold)
        )

    def _queue_stage(
        self, pipeline_name: str, stage: dict, state: dict, prev_output: str, queue: TaskQueue
    ) -> str:
        template = stage.get("prompt_template", "")
        prompt = (
            template
            .replace("{prev_output}", prev_output)
            .replace("{run_id}", state["run_id"])
            .replace("{topic}", state.get("topic", ""))
        )

        # Map stage task_type string to TaskType enum
        type_str = stage.get("task_type", "general")
        try:
            task_type = TaskType(type_str)
        except ValueError:
            task_type = TaskType.GENERAL

        output_file = stage.get("output_file", "")
        if output_file:
            output_file = output_file.replace("{run_id}", state["run_id"])

        task = Task(
            prompt=prompt,
            type=task_type,
            output_file=output_file,
        )
        task.id = f"{state['run_id']}-{stage.get('id', 'stage')}"

        task_file = self.tasks_dir / f"{task.id}.json"
        task_file.write_text(
            json.dumps({
                "id": task.id,
                "type": task_type.value,
                "prompt": prompt,
                "output_file": output_file,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat(),
                "result": "",
                "error": "",
                "model_used": "",
                "completed_at": "",
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        queue.sync_json_to_db() if hasattr(queue, "sync_json_to_db") else None
        return task.id

    def _save_state(self, state_file: Path, state: dict) -> None:
        state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    def _mark_done(self, state_file: Path, state: dict) -> None:
        state["status"] = "done"
        state["ended_at"] = datetime.utcnow().isoformat()
        self._save_state(state_file, state)
        print(f"[PipelineController] Pipeline '{state['pipeline']}' run '{state['run_id']}' complete.")
