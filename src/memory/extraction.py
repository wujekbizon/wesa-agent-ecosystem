"""Extraction — turn task text into MemoryCandidates for the promotion gate.

Two implementations behind one shape:
  - RuleBasedExtractor: regex over WESA-specific fact shapes. No API cost,
    low recall. The thin safety net.
  - (LLMExtractor lands later — it carries the real recall, using the same
    assembled prompt as the task call so it reuses the warm cache.)

Per the plan, candidates are proposed as `inferred` so they enter the fact
store `provisional` (invisible to retrieval until Axel curates or a second
source confirms). Only Greg/Axel-authored candidates write `active` directly.
"""
from __future__ import annotations

import re

from src.memory.records import MemoryCandidate

# infra endpoints: "<thing> at 192.168.1.62" / "running on http://host:port"
_HOST_PATTERN = re.compile(
    r"(?P<subject>[A-Za-z][\w-]*)\s+(?:is\s+)?(?:at|on|runs on|running on)\s+"
    r"(?P<value>(?:https?://)?\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?|https?://\S+)",
    re.IGNORECASE,
)

# model assignment: "D1 on qwen3.5-..." / "D1 uses model gpt-..."
_MODEL_PATTERN = re.compile(
    r"\b(?P<agent>[A-Za-z]{1,3}\d{1,2})\b\s+(?:on|uses|running)\s+(?:model\s+)?"
    r"(?P<value>[\w.\-:]+)",
    re.IGNORECASE,
)


class RuleBasedExtractor:
    """Crude on purpose. Proposes provisional infra/model facts."""

    def extract(
        self,
        *,
        text: str,
        tenant_id: str = "wesa",
        source_run_id: str,
        source_turn_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[MemoryCandidate]:
        out: list[MemoryCandidate] = []

        for m in _HOST_PATTERN.finditer(text):
            subject = f"infrastructure:{m.group('subject').lower()}"
            out.append(MemoryCandidate(
                memory_type="fact", tenant_id=tenant_id,
                content=f"{m.group('subject')} is at {m.group('value')}",
                confidence=0.8, source_run_id=source_run_id,
                source_turn_id=source_turn_id, subject=subject,
                predicate="network", source="inferred",
            ))

        for m in _MODEL_PATTERN.finditer(text):
            agent = m.group("agent").lower()
            out.append(MemoryCandidate(
                memory_type="fact", tenant_id=tenant_id,
                content=f"{m.group('agent')} runs model {m.group('value')}",
                confidence=0.8, source_run_id=source_run_id,
                source_turn_id=source_turn_id, subject=f"agent:{agent}",
                predicate="model", source="inferred",
            ))

        return out
