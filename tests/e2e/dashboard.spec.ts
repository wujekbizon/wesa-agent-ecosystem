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
    // The task queue renders a table with column headers; ID is always present
    const idHeader = page.getByRole('columnheader', { name: 'ID' });
    await expect(idHeader).toBeVisible();
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
