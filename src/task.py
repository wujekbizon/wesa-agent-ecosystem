"""
WESA Agent Ecosystem - Task Model
A task is a JSON file dropped into the tasks/ folder.
The daemon picks it up, processes it, and writes results to logs/.
"""

import json
import uuid
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path


class TaskType(str, Enum):
    CODE = "code"          # → Ollama (D1 - Python, backend, CLI)
    RESEARCH = "research"  # → Gemini (R1 - web research, analysis)
    WRITE = "write"        # → Gemini (W1 - docs, content, markdown)
    ANALYZE = "analyze"    # → Gemini
    GENERAL = "general"    # → Gemini
    DESIGN = "design"      # → Gemini (UX1 - UI/UX prototypes)
    FRONTEND = "frontend"  # → Gemini (FE1 - React 19, Next.js)
    TEST = "test"          # → Gemini (T1 - Playwright, testing)
    REVIEW = "review"      # → Gemini (Q1 - design critic, design reviews only)
    CODE_REVIEW = "code_review"  # → Gemini (QA - code QA, logic errors, test generation)
    ASSETS = "assets"      # → Gemini (A1 - image gen via Nano Banana)
    SKILL_UPDATE = "skill_update"  # → Gemini (SU1 - writes per-run overlay during design loop)
    SKILL_MERGE = "skill_merge"    # → Gemini (SU1 - permanent merge to ux_agent.yaml on approval)
    MANAGE = "manage"              # → Gemini (PM1 - project manager, FE1+T1 loop)
    COORDINATE = "coordinate"      # → Gemini (PM1 - coordination tasks)


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Task:
    prompt: str
    type: TaskType = TaskType.GENERAL
    id: str = ""
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = ""
    completed_at: str = ""
    result: str = ""
    error: str = ""
    model_used: str = ""
    output_file: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_file(cls, path: Path) -> "Task":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

    def save(self, path: Path):
        path.write_text(self.to_json(), encoding="utf-8")
