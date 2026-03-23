<#
.SYNOPSIS
    Sets up Playwright and initializes the test suite for the WESA Agent Ecosystem Dashboard.
.DESCRIPTION
    Checks for Node.js, initializes npm if needed, installs Playwright dependencies,
    installs the Chromium browser binary, and generates standard configuration and test files.
.EXAMPLE
    .\setup_playwright.ps1
#>

$ErrorActionPreference = 'Stop'
$TestDir = $PSScriptRoot  # Script lives inside tests/ already

Write-Host "=== WESA Agent Ecosystem - Playwright Setup ===" -ForegroundColor Cyan

# 1. Check Node.js installation
Write-Host "Checking Node.js installation..." -ForegroundColor Yellow
try {
    $nodeVersion = & node --version 2>&1
    Write-Host "Found Node.js version: $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Node.js is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Please install Node.js (v18+) from https://nodejs.org/ and restart your terminal."
    exit 1
}

# 2. Initialize npm if package.json doesn't exist
$packageJsonPath = Join-Path $TestDir "package.json"
if (-not (Test-Path $packageJsonPath)) {
    Write-Host "Initializing npm project in $TestDir..." -ForegroundColor Yellow
    Set-Location $TestDir
    & npm init -y
} else {
    Write-Host "package.json already exists. Skipping initialization." -ForegroundColor Gray
}

# 3. Install Playwright as dev dependency
Write-Host "Installing @playwright/test..." -ForegroundColor Yellow
& npm install --save-dev @playwright/test

# 4. Install Chromium browser
# Note: We install only 'chromium' to keep setup lightweight and fast for CI/CD or local dev.
Write-Host "Downloading Chromium browser binary..." -ForegroundColor Yellow
& npx playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install Chromium. Ensure you have network access." -ForegroundColor Red
    exit 1
}

# 5. Create playwright.config.ts
Write-Host "Generating playwright.config.ts..." -ForegroundColor Yellow
$configContent = @'
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  expect: {
    timeout: 5000,
  },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:8765',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
'@

Set-Location $TestDir
if (-not (Test-Path "./e2e")) {
    New-Item -ItemType Directory -Path "./e2e" | Out-Null
}
$configPath = Join-Path $TestDir "playwright.config.ts"
$configContent | Out-File -FilePath $configPath -Encoding utf8

# 6. Create dashboard.spec.ts
Write-Host "Generating dashboard.spec.ts..." -ForegroundColor Yellow
$testContent = @'
import { test, expect } from '@playwright/test';

test.describe('Dashboard Home Page', () => {
  test('dashboard loads', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/WESA/);
  });

  test('daemon health section visible', async ({ page }) => {
    await page.goto('/');
    const healthSection = page.getByText('DAEMON HEALTH');
    await expect(healthSection).toBeVisible();
  });

  test('task queue section visible', async ({ page }) => {
    await page.goto('/');
    const queueSection = page.getByText('TASK QUEUE');
    await expect(queueSection).toBeVisible();
  });

  test('websocket connects', async ({ page }) => {
    const logs: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'warning' || msg.type() === 'error') {
        logs.push(msg.text());
      }
    });

    await page.goto('/');
    
    // Allow initial connection handshake
    await page.waitForTimeout(2000);

    const wsErrors = logs.filter(log => log.toLowerCase().includes('websocket'));
    expect(wsErrors).toHaveLength(0, 'No WebSocket errors should be logged after connection');
  });
});
'@

$specPath = Join-Path $TestDir "e2e/dashboard.spec.ts"
$testContent | Out-File -FilePath $specPath -Encoding utf8

Write-Host "==========================================" -ForegroundColor Green
Write-Host "Playwright setup complete. Run: npx playwright test" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green