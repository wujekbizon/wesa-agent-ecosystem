# Playwright Guide — WESA Agent Ecosystem
**For:** Greg (one-time setup + ongoing test runs)
**Prerequisite:** Node.js v18+ installed. Check with `node --version` in terminal.

---

## One-Time Setup

Open PowerShell in the repo root (`wesa-agent-ecosystem/`) and run:

```powershell
.\tests\setup_playwright.ps1
```

This installs `@playwright/test`, downloads the Chromium browser binary, and writes the config and initial test files. Takes 1-2 minutes. You only do this once.

If you see `ERROR: Node.js is not installed` — download it from https://nodejs.org (LTS version), install, restart PowerShell, then re-run the script.

---

## Running Tests

The dashboard (`web_monitor.py`) must be running on port 8765 before you run tests.

```powershell
# From inside the tests/ folder:
cd tests
npx playwright test
```

Or from the repo root:

```powershell
npx playwright test --config=tests/playwright.config.ts
```

---

## What the Tests Check

| Test | What it does |
|---|---|
| `dashboard loads` | Opens the dashboard, confirms the page title contains "WESA" |
| `daemon health section visible` | Confirms "DAEMON HEALTH" panel is rendered |
| `task queue section visible` | Confirms "TASK QUEUE" panel is rendered |
| `websocket connects` | Waits 2 seconds after load, checks no WebSocket errors in console |

All 4 tests pass = dashboard is structurally correct and WebSocket is alive.

---

## Reading Results

After the run, Playwright opens an HTML report automatically. If it doesn't:

```powershell
npx playwright show-report
```

Failed tests show a screenshot of exactly what broke. Green = pass, Red = fail.

---

## Adding New Tests

Tests live in `tests/e2e/dashboard.spec.ts`. Each test follows this pattern:

```typescript
test('my test name', async ({ page }) => {
  await page.goto('/');
  // find something
  const element = page.getByText('SOME TEXT');
  // assert it
  await expect(element).toBeVisible();
});
```

Common assertions:
- `await expect(element).toBeVisible()` — element exists and is on screen
- `await expect(page).toHaveTitle(/text/)` — page title matches
- `await expect(element).toHaveText('exact text')` — element has exact content
- `await expect(element).toBeEnabled()` — button/input is clickable

To click something: `await page.getByText('SUBMIT').click()`
To fill a form field: `await page.getByPlaceholder('Task brief').fill('my task')`
To wait: `await page.waitForTimeout(1000)` (milliseconds)

---

## When T1 Runs Tests (Automated)

Once PM1 + T1 pipeline is active, T1 will:
1. Receive a test brief from PM1
2. Add new test cases to `dashboard.spec.ts`
3. Report results back to PM1 as a structured pass/fail log

You do not need to run tests manually once that loop is running. You only run manually when you want an immediate check.

---

## Troubleshooting

**"Cannot connect to localhost:8765"** — Dashboard is not running. Start it first: `python web_monitor.py`

**"Chromium executable not found"** — Re-run setup: `npx playwright install chromium`

**Tests time out** — Dashboard is slow to load. Increase timeout in `playwright.config.ts`: change `timeout: 30000` to `timeout: 60000`

**PowerShell execution policy error** — Run: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` then retry

---
*Axel — 2026-03-23*
