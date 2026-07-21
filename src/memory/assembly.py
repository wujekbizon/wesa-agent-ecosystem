"""Level 3 — assemble an agent prompt from memory, every turn, within a token
budget.

The shift from Levels 1-2: instead of stuffing a static AGENTS.md at the top of
a prompt, the prompt is *reconstructed* each turn from typed memory:

  reserved slots  — policies + applicable preferences. Always included; cheap
                    and they always apply.
  ranked slots    — facts + episodes for this task, in relevance order, added
                    until the token budget runs out. Over budget -> drop whole
                    least-relevant items (compact, never truncate mid-record).

This keeps prompt size bounded regardless of how much memory accumulates. For
WESA's task-queue workload the win is mostly "the right slice per task" rather
than long-conversation bounding (see plan §13.2), but the mechanism is the same.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PROMPT_PREAMBLE = """\
You are a WESA agent operating with structured memory. The <context> block is
assembled fresh each turn. Treat each section by these rules:

<policies>: binding constraints. If a request violates one, refuse and name the
policy. Do not work around it.
<preferences>: how to behave (format, language, verbosity). Honour them.
<facts>: your authoritative record. If the user/task contradicts a fact, surface
the discrepancy; don't silently overwrite. Higher [relevance] = more pertinent.
<episodes>: summaries of past completed work — use to recognise "seen this
before"; they describe what happened, not what is currently true.
"""


def token_estimate(text: str) -> int:
    """Cheap, dependency-free token estimate (~4 chars/token)."""
    return max(1, len(text) // 4)


@dataclass
class AssembledPrompt:
    text: str
    tokens: int
    retrieval_mode: str
    facts_kept: int
    episodes_kept: int
    facts_dropped: int
    episodes_dropped: int
    policies: int = 0
    preferences: int = 0


def _render_reserved(policies: list[dict], preferences: list[dict]) -> list[str]:
    parts: list[str] = []
    if policies:
        parts.append("  <policies>")
        for p in policies:
            parts.append(f"    {p['key']}={p['value']}")
        parts.append("  </policies>")
    if preferences:
        parts.append("  <preferences>")
        for p in preferences:
            parts.append(f"    {p['key']}={p['value']}")
        parts.append("  </preferences>")
    return parts


def _fact_line(f) -> str:
    return (f"    [{f.relevance}] subject={f.extra.get('subject','')} "
            f"predicate={f.extra.get('predicate','')} :: {f.text}")


def _episode_line(e) -> str:
    return f"    [{e.relevance}] [{e.extra.get('task_type','')}] {e.extra.get('title','')}: {e.text}"


def assemble_prompt(
    *,
    policies: list[dict],
    preferences: list[dict],
    facts: list,          # ranked list[RetrievedRow]
    episodes: list,       # ranked list[RetrievedRow]
    retrieval_mode: str = "none",
    token_budget: int = 2000,
    include_preamble: bool = True,
) -> AssembledPrompt:
    """Build the prompt within token_budget. Reserved slots (preamble, policies,
    preferences) always go in; ranked slots (facts, then episodes) are added in
    order while they fit, skipping items too large to leave room for smaller
    later ones."""
    head: list[str] = []
    if include_preamble:
        head.append(PROMPT_PREAMBLE)
    head.append("<context>")
    reserved = _render_reserved(policies, preferences)

    reserved_text = "\n".join(head + reserved)
    used = token_estimate(reserved_text)

    kept_facts: list[str] = []
    fk = fd = 0
    for f in facts:
        line = _fact_line(f)
        cost = token_estimate(line)
        if used + cost <= token_budget:
            kept_facts.append(line)
            used += cost
            fk += 1
        else:
            fd += 1

    kept_eps: list[str] = []
    ek = ed = 0
    for e in episodes:
        line = _episode_line(e)
        cost = token_estimate(line)
        if used + cost <= token_budget:
            kept_eps.append(line)
            used += cost
            ek += 1
        else:
            ed += 1

    body = list(head) + reserved
    if kept_facts:
        body.append("  <facts>")
        body.extend(kept_facts)
        body.append("  </facts>")
    if kept_eps:
        body.append("  <episodes>")
        body.extend(kept_eps)
        body.append("  </episodes>")
    body.append("</context>")
    text = "\n".join(body)
    return AssembledPrompt(
        text=text, tokens=token_estimate(text), retrieval_mode=retrieval_mode,
        facts_kept=fk, episodes_kept=ek, facts_dropped=fd, episodes_dropped=ed,
        policies=len(policies), preferences=len(preferences),
    )
