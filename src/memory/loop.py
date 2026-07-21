"""Level 3 — the per-task memory loop and trace envelope.

Two halves:
  build_task_prompt(manager, ...) — read path. Path A (policies+prefs) + Path B
    (facts+episodes) assembled into one token-budgeted prompt.
  post_task(manager, ...)         — write path. After a task completes: log the
    completion to trace, run extraction over the task text, push each candidate
    through the promotion gate, and log a promotion_result trace per candidate.
    Returns a trace envelope (what was retrieved, what was promoted) for replay
    and audit.

Extraction is the thin rule-based safety net by default; pass any object with
an `.extract(...)` method to swap in the LLM extractor later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.memory.assembly import AssembledPrompt, assemble_prompt
from src.memory.extraction import RuleBasedExtractor


@dataclass
class TraceEnvelope:
    run_id: str
    retrieval_mode: str
    facts_returned: list[str] = field(default_factory=list)
    episodes_returned: list[str] = field(default_factory=list)
    preferences_applied: list[str] = field(default_factory=list)
    promotions: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "retrieval": {
                "facts_returned": self.facts_returned,
                "episodes_returned": self.episodes_returned,
                "preferences_applied": self.preferences_applied,
                "retrieval_mode": self.retrieval_mode,
            },
            "promotions": self.promotions,
        }


def build_task_prompt(
    manager,
    *,
    query_text: str,
    agent_id: str | None = None,
    user_id: str | None = "greg",
    token_budget: int = 2000,
) -> tuple[AssembledPrompt, TraceEnvelope]:
    """Assemble the full per-task prompt from memory and return it alongside a
    trace envelope describing what was surfaced."""
    startup = manager.session_startup(user_id=user_id, agent_id=agent_id)
    task = manager.task_context(query_text, agent_id=agent_id)

    prompt = assemble_prompt(
        policies=startup.policies,
        preferences=startup.preferences,
        facts=task.facts,
        episodes=task.episodes,
        retrieval_mode=task.retrieval_mode,
        token_budget=token_budget,
    )
    env = TraceEnvelope(
        run_id="",  # filled by caller when a run_id is known
        retrieval_mode=task.retrieval_mode,
        facts_returned=[f.id for f in task.facts],
        episodes_returned=[e.id for e in task.episodes],
        preferences_applied=[p["key"] for p in startup.preferences],
    )
    return prompt, env


def post_task(
    manager,
    *,
    run_id: str,
    task_text: str,
    result: str = "",
    agent_id: str | None = None,
    extractor: Any | None = None,
    outcome: str = "task_completed",
) -> TraceEnvelope:
    """Capture memory from a finished task: trace the completion, extract
    candidates, promote them through the gate, and trace each promotion."""
    extractor = extractor or RuleBasedExtractor()
    env = TraceEnvelope(run_id=run_id, retrieval_mode="n/a")

    manager.trace.append(
        run_id=run_id, event_type=outcome, agent_id=agent_id,
        payload={"task_text": task_text[:500], "result": result[:500]},
        commit=True,
    )

    candidates = extractor.extract(
        text=f"{task_text}\n{result}", tenant_id=manager.tenant_id,
        source_run_id=run_id, agent_id=agent_id,
    )
    for cand in candidates:
        res = manager.gate.promote(cand)
        entry = {
            "type": cand.memory_type,
            "subject": cand.subject,
            "outcome": res.outcome,
            "status": res.status,
            "record_id": res.record_id,
            "reason": res.reason,
        }
        env.promotions.append(entry)
        manager.trace.append(
            run_id=run_id, event_type="promotion_result", agent_id=agent_id,
            payload=entry, commit=True,
        )
    return env
