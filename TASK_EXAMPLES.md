# Task Examples — Quick Reference

Two ways to submit a task:

**A — Drop a JSON file** into `tasks/`. Daemon picks it up within 3 seconds.
**B — Web monitor** at `localhost:8765` → Submit Task form → fill in `type` + `prompt` → hit Submit.

For JSON: change `id` to something unique before each drop — duplicate IDs are skipped.
`output_file` is optional but recommended — daemon writes result directly to that path.

---

## D1 — Developer (Python, backend, CLI)
`type: code` → Ollama (qwen3.5:35b)

> **Monitor use case**: Fix a bug, add an endpoint, write a utility script, modify an existing file. Keep to one file per task.

```json
{
  "id": "d1-001",
  "type": "code",
  "prompt": "Write a Python function that reads all .json files from the tasks/ folder and returns a list of dicts with id, type, and status fields only. Save it to src/utils/task_reader.py.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00"
}
```

```json
{
  "id": "d1-002",
  "type": "code",
  "prompt": "Add a /api/stats endpoint to web_monitor.py that returns a JSON object with counts of tasks by status (pending, in_progress, done, failed). Query from tasks.db.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00"
}
```

---

## W1 — Writer (docs, content, markdown)
`type: write` → Gemini (gemini-3-flash-preview)

> **Monitor use case**: Generate documentation, write a YAML skill file, produce a report or structured markdown document.

```json
{
  "id": "w1-001",
  "type": "write",
  "prompt": "Write a concise README section explaining how the task queue works: how to submit a task, what fields are required, how the daemon picks it up, and where the output goes. Markdown format. Max 300 words.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/w1-001_report.md"
}
```

```json
{
  "id": "w1-002",
  "type": "write",
  "prompt": "Write a PM1 skill YAML for an agent whose role is Project Manager. It manages FE1 and T1 in a build-test loop and reports to Axel. Include: name, role, model, task_types, system_prompt (detailed — what it does, how it prioritises, how it reports). Output the full YAML only.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/w1-002_report.md"
}
```

---

## R1 — Researcher (web, analysis, trends)
`type: research` → Gemini (gemini-3.1-pro-preview, web search enabled)

> **Monitor use case**: Anything requiring live web data — competitor research, framework analysis, market trends, technical specs. R1 searches the real web, not training data.

```json
{
  "id": "r1-001",
  "type": "research",
  "prompt": "Research the current state of MCP (Model Context Protocol) adoption in 2026. What are the major frameworks supporting it? What are the most popular MCP servers available? Any notable limitations or criticisms? Structure: Overview, Adoption, Top Servers, Limitations.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/r1-001_report.md"
}
```

```json
{
  "id": "r1-002",
  "type": "research",
  "prompt": "Research 5 competing AI agent marketplace platforms launching in 2025-2026. For each: what they offer, pricing model, target customer, tech stack if known, and what makes them different from a job marketplace like Upwork. Structured markdown report.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/r1-002_report.md"
}
```

---

## Q1 — Design Critic (scoring, rubric review)
`type: review` → Gemini (gemini-3.1-pro-preview)

> **Monitor use case**: Score a design file after UX1 produces it, or review any HTML prototype. Point it at a file path in the prompt.

```json
{
  "id": "q1-001",
  "type": "review",
  "prompt": "Review and score the HTML dashboard design at static/dashboard.html on 8 dimensions: Visual Hierarchy, Typography, Color & Contrast, Layout & Spacing, Component Consistency, Data Clarity, Interactivity Feel, Professional Polish. Score each 1-10. Give 2-3 specific improvement notes per dimension. End with total score and top 3 priority fixes.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/q1-001_report.md"
}
```

```json
{
  "id": "q1-002",
  "type": "review",
  "prompt": "Review the HTML prototype at static/designs/ux1-001_concept.html using the same 8-dimension rubric. Score 1-10 per dimension, 2-3 improvement notes each, total score, top 3 fixes. Be critical — target is 9/10 average.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/q1-002_report.md"
}
```

---

## UX1 — Design Specialist (UI/UX, HTML prototypes)
`type: design` → Gemini (gemini-3-flash-preview)

> **Monitor use case**: Generate a new UI concept or prototype. Give it a design direction and target — it outputs a self-contained HTML file. Use `output_file` pointing to `static/designs/` so it's viewable in the Designs tab.

```json
{
  "id": "ux1-001",
  "type": "design",
  "prompt": "Design a dark-theme HTML/CSS/JS single-page dashboard for an AI agent monitoring tool. Show: active tasks list with status badges, live log feed panel, agent status cards (name, model, last active). Modern SaaS aesthetic. Vanilla HTML/CSS/JS only — no frameworks. Output: complete self-contained HTML file.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/static/designs/ux1-001_concept.html"
}
```

```json
{
  "id": "ux1-002",
  "type": "design",
  "prompt": "Design a landing page hero section for AgentHire — a marketplace where companies hire AI agent teams. Target: tech-forward businesses. Key message: hire specialized AI agents like you hire contractors. Dark background, minimal, professional. Vanilla HTML/CSS only. Output: self-contained HTML file.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/static/designs/ux1-002_hero.html"
}
```

---

## FE1 — Frontend Expert (React 19, Next.js 16)
`type: frontend` → Gemini (gemini-3-flash-preview)

> **Monitor use case**: Write React components, Server Actions, or Next.js pages for AgentHire. Always specify the target file path in the prompt. FE1 follows the Server Components first rule — remind it if needed.

```json
{
  "id": "fe1-001",
  "type": "frontend",
  "prompt": "Write a React 19 Server Component for an agent status card. Props: agentId (string), agentName (string), model (string), lastActive (Date), taskCount (number), status ('active' | 'idle' | 'error'). Style with Tailwind 4. Pure display component, no client state. TypeScript. Default export.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/fe1-001_report.md"
}
```

```json
{
  "id": "fe1-002",
  "type": "frontend",
  "prompt": "Write a Next.js 16 page at app/(dashboard)/agents/page.tsx that renders a grid of AgentCard Server Components. Data is fetched server-side from the C# API at /api/agents. Use React 19 streaming with Suspense. Tailwind 4 styling. TypeScript strict.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/fe1-002_report.md"
}
```

---

## T1 — Tester (Playwright, QA)
`type: test` → PlaywrightAgent (writes + runs npx playwright test)

> **Monitor use case**: Test any feature on the running dashboard. Dashboard must be up at localhost:8765 before T1 fires. T1 writes the tests, runs them, and returns a pass/fail report.

```json
{
  "id": "t1-001",
  "type": "test",
  "prompt": "Test that the dashboard at localhost:8765 loads correctly. Check: page title contains 'WESA', the main task table is visible, at least one status badge is present, and the WebSocket connection indicator shows connected. Write Playwright TypeScript tests.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/t1-001_report.md"
}
```

```json
{
  "id": "t1-002",
  "type": "test",
  "prompt": "Test the task submission form on the dashboard at localhost:8765. Check: form is visible, required fields (type, prompt) are present, submitting empty fields shows a validation error, submitting a valid task shows a success confirmation. Write Playwright TypeScript tests.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/t1-002_report.md"
}
```

---

## A1 — Assets Creator (image generation via Nano Banana Pro)
`type: assets` → Gemini (Gemini 2.5 Flash)

> **Monitor use case**: Request a logo, icon, or illustration concept. A1 produces a detailed image generation prompt ready to use with the Nano Banana Pro API.

```json
{
  "id": "a1-001",
  "type": "assets",
  "prompt": "Generate a logo concept for AgentHire — an AI agent marketplace. Style: minimal, modern, tech. Colors: dark navy + electric blue accent. The logo should suggest a network of agents or connections. Describe the concept and provide the exact image generation prompt to use with Nano Banana Pro API.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/a1-001_report.md"
}
```

---

## G1 — Git Agent (staging, commit messages)
`type: git` → Ollama (qwen3.5:35b)

> **Monitor use case**: Stage changed files and get a conventional commit message ready to paste. Greg commits from Windows — G1 just prepares the message.

```json
{
  "id": "g1-001",
  "type": "git",
  "prompt": "Stage all modified Python files in src/ and write a conventional commit message for the changes. Format: type(scope): description. Include a short body explaining what changed and why. Do not commit — stage only and output the commit message.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/g1-001_report.md"
}
```

---

## PM1 — Project Manager (coordinates FE1 + T1)
`type: manage` → Gemini (gemini-3-flash-preview)

> **Monitor use case**: Hand PM1 a feature description — it breaks it into FE1 build task + T1 test task and outputs both JSON blocks ready to drop into the queue.

```json
{
  "id": "pm1-001",
  "type": "manage",
  "prompt": "Plan a build-test sprint for this feature: add a pipeline status panel to the dashboard showing current pipeline name, active stage, iteration count, and last Q1 score. Output two task JSON blocks: one for FE1 (what to build, acceptance criteria) and one for T1 (what to test). Ready to drop into the queue.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/pm1-001_report.md"
}
```

---

## SU1 — Skill Updater (rewrites agent YAML from Q1 reviews)
`type: skill-update` → Gemini (Gemini 2.5 Flash)

> **Monitor use case**: After a Q1 review lands in logs/designs/, send SU1 to extract rules and update the UX agent YAML. Always runs after Q1 in the design loop — but can be triggered manually too.

```json
{
  "id": "su1-001",
  "type": "skill-update",
  "prompt": "Read the latest Q1 design review in logs/designs/ and extract 3-5 actionable improvement rules for UX1. Update the relevant section of agents/skills/ux_agent.yaml. Rules must be prescriptive ('always use...', 'never use...', 'prefer...'). Output: updated YAML content only.",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00",
  "output_file": "WesaTeam/agent-ecosystem/logs/su1-001_report.md"
}
```

---

## Pipelines

Pipelines are multi-stage automated loops defined in `agents/pipelines/*.yaml`. Available: `design-loop`, `research-center`.

### Option A — Trigger via task JSON (drop into tasks/)

```json
{
  "id": "pipeline-001",
  "type": "general",
  "prompt": "START_PIPELINE:design-loop TOPIC:WESA agent ecosystem dashboard — modern monitoring tool UI",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00"
}
```

```json
{
  "id": "pipeline-002",
  "type": "general",
  "prompt": "START_PIPELINE:research-center TOPIC:AI agent frameworks and orchestration tools in 2026",
  "status": "pending",
  "created_at": "2026-03-26T10:00:00"
}
```

### Option B — Trigger via web monitor API (curl or browser)

```bash
# Start design loop
curl -X POST http://localhost:8765/api/pipeline/start \
  -H "Content-Type: application/json" \
  -d '{"pipeline": "design-loop", "topic": "WESA agent ecosystem dashboard — modern monitoring tool UI"}'

# Start research center
curl -X POST http://localhost:8765/api/pipeline/start \
  -H "Content-Type: application/json" \
  -d '{"pipeline": "research-center", "topic": "AI agent frameworks and orchestration tools in 2026"}'

# Check running pipelines
curl http://localhost:8765/api/pipelines/running
```

### Option C — Monitor dashboard

Go to `localhost:8765` → Pipelines tab → select pipeline name → enter topic → Start.

> **design-loop topic examples**: `"AgentHire portal landing page — marketplace for AI agent teams"` / `"WESA dashboard redesign — dark theme monitoring tool"` / `"AgentHire agent profile card component"`

> **research-center topic examples**: `"MCP server ecosystem and adoption in 2026"` / `"AI agent hiring and marketplace business models"` / `"Next.js 16 and React 19 Server Components best practices"`

---

## Tips

- Change `id` to something unique before each drop — duplicate IDs are deduped and skipped
- `output_file` path is relative to `Coworker/` — always prefix with `WesaTeam/agent-ecosystem/...`
- Watch `logs/AXEL_BRIEFING.md` or the dashboard at `localhost:8765` for task completion
- D1 tasks: one file change per task — multi-file tasks increase hallucination risk
- T1 tasks: dashboard must be running at localhost:8765 before the task fires
