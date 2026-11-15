import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { ObjectLookup } from '../../src/types/objects';
import { API_URL, OFFLINE_URL } from '../servers';

const directory = fileURLToPath(new URL('../../../data/cache/objects', import.meta.url));
const saved: ObjectLookup[] = existsSync(directory) ? readdirSync(directory).filter(name => name.endsWith('.json')).map(name => JSON.parse(readFileSync(`${directory}/${name}`, 'utf8'))) : [];
const sirius = saved.find(result => result.candidates[0]?.name === '* alf CMa');

async function coordinates(page: Page, ra: string, dec: string) {
  await page.getByLabel('Right ascension in degrees').fill(ra);
  await page.getByLabel('Declination in degrees').fill(dec);
  await page.getByRole('button', { name: 'Go to location', exact: true }).click();
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/series?*', route => route.fulfill({ json: { state: 'empty', frames: [], notes: [] } }));
  await page.route('**/api/observation-metadata?*', route => route.fulfill({ json: { observations: [], source: 'NASA IRSA TAP', limited: false, cached: false } }));
});

test('a real map click opens cached SIMBAD information, supports multiple matches, and clears on deselection', async ({ page }) => {
  test.skip(!sirius, 'Cache real Sirius metadata with python -m backend.objects --ra 101.287155 --dec -16.716116.');
  const errors: string[] = [];
  const lookups: URL[] = [];
  page.on('pageerror', error => errors.push(error.message));
  // The click is near Sirius. Read the actual cached backend result at the
  // canonical coordinate so pixel rounding never adds an external dependency.
  await page.route('**/api/objects?*', async route => {
    lookups.push(new URL(route.request().url()));
    const response = await route.fetch({ url: `${API_URL}/api/objects?ra=${sirius!.ra}&dec=${sirius!.dec}&cache_only=true` });
    await route.fulfill({ response });
  });
  await page.goto('/');
  await expect(page.locator('.map-stage')).toHaveAttribute('data-status', 'ready', { timeout: 60000 });
  await coordinates(page, String(sirius!.ra), String(sirius!.dec));
  await expect(page.getByTestId('object-name')).toHaveText('* alf CMa');
  for (let step = 0; step < 4; step++) await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  const beforeClick = lookups.length;
  await page.getByRole('button', { name: 'Close object information' }).click();
  await expect(page.getByLabel('Object information', { exact: true })).toHaveCount(0);
  const map = page.locator('.aladin-catalogCanvas');
  await map.scrollIntoViewIfNeeded();
  const bounds = (await map.boundingBox())!;
  await map.click({ position: { x: bounds.width / 2, y: bounds.height / 2 } });
  await expect(page.getByTestId('object-name')).toHaveText('* alf CMa');
  expect(lookups.length).toBeGreaterThan(beforeClick);
  expect(Number(lookups.at(-1)!.searchParams.get('ra'))).toBeCloseTo(sirius!.ra, 2);
  expect(Number(lookups.at(-1)!.searchParams.get('dec'))).toBeCloseTo(sirius!.dec, 2);
  await expect(page.getByTestId('object-type')).toHaveText(sirius!.candidates[0].object_type);
  await page.getByLabel('Catalog match', { exact: true }).selectOption('1');
  await expect(page.getByTestId('object-name')).toHaveText(sirius!.candidates[1].name);
  await expect(page.getByRole('link', { name: 'View SIMBAD record' })).toHaveAttribute('href', sirius!.candidates[1].source_url);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId('object-name')).toHaveText(sirius!.candidates[1].name);
  const popup = page.getByRole('dialog', { name: 'Selected sky object' });
  const boundsAfterResize = (await popup.boundingBox())!;
  expect(boundsAfterResize.x).toBeGreaterThanOrEqual(0);
  expect(boundsAfterResize.x + boundsAfterResize.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await popup.screenshot({ path: 'artifacts/object-information-mobile.png' });
  await page.getByRole('button', { name: 'Close object information' }).click();
  await expect(page.getByLabel('Object information', { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('lookup failure can be retried and an empty result is shown honestly', async ({ page }) => {
  await page.route('**/api/health', route => route.fulfill({ json: { offline: true } }));
  let calls = 0;
  await page.route('**/api/objects?*', route => {
    if (++calls === 1) return route.fulfill({ status: 503, json: { detail: 'Catalog temporarily unavailable' } });
    return route.fulfill({ json: { candidates: [], radius_arcsec: 30, total_matches: 0, search_limited: false,
      source: 'SIMBAD test transport', retrieved_at: '2026-11-14T00:00:00Z', cached: false } });
  });
  await page.goto('/');
  await coordinates(page, '10', '-20');
  await expect(page.getByText('Catalog temporarily unavailable', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Retry object lookup' }).click();
  await expect(page.getByText(/No catalog match within 30 arcseconds/)).toBeVisible();
  await expect(page.getByTestId('object-name')).toHaveCount(0);
  expect(calls).toBe(2);
});

test('a late object lookup cannot overwrite a newer selection or a cleared selection', async ({ page }) => {
  test.skip(!sirius, 'Requires a real saved SIMBAD result.');
  await page.route('**/api/health', route => route.fulfill({ json: { offline: true } }));
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  let firstStarted: () => void = () => {};
  const started = new Promise<void>(resolve => { firstStarted = resolve; });
  await page.route('**/api/objects?*', async route => {
    if (new URL(route.request().url()).searchParams.get('ra') === '10') {
      firstStarted(); await gate;
      try { await route.fulfill({ json: sirius }); } catch { /* Intentionally aborted by a new click. */ }
    } else await route.fulfill({ json: { ...sirius, candidates: [], total_matches: 0 } });
  });
  await page.goto('/');
  await coordinates(page, '10', '-20');
  await started;
  await coordinates(page, '20', '-20');
  await expect(page.getByText(/No catalog match within 30 arcseconds/)).toBeVisible();
  release();
  await page.waitForTimeout(400);
  await expect(page.getByTestId('object-name')).toHaveCount(0);
  await expect(page.getByTestId('selected-ra')).toHaveText('20.000000°');
  await page.getByRole('button', { name: 'Close object information' }).click();
  await expect(page.getByLabel('Object information', { exact: true })).toHaveCount(0);
});

test('saved object information works on a fresh forced-offline backend without external requests', async ({ page }) => {
  test.skip(!sirius, 'Requires cached Sirius metadata and a built frontend.');
  const external: string[] = [];
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname !== '127.0.0.1') { external.push(url.href); return route.abort('internetdisconnected'); }
    return route.continue();
  });
  await page.goto(OFFLINE_URL);
  await coordinates(page, String(sirius!.ra), String(sirius!.dec));
  await expect(page.getByTestId('object-name')).toHaveText('* alf CMa');
  await expect(page.getByLabel('Object information', { exact: true })).toContainText('saved locally');
  await page.reload();
  await coordinates(page, String(sirius!.ra), String(sirius!.dec));
  await expect(page.getByTestId('object-name')).toHaveText('* alf CMa');
  await coordinates(page, '1.234567', '12.345678');
  await expect(page.getByText(/Object information for this coordinate is not saved locally/)).toBeVisible();
  expect(external).toEqual([]);
});
