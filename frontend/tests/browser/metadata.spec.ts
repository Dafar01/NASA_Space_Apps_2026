import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const catalog = { candidates: [], radius_arcsec: 30, total_matches: 0, source: 'SIMBAD test transport', retrieved_at: '2026-11-14T00:00:00Z', cached: false, search_limited: false };
const response = { source: 'NASA IRSA SIA', observations: [{ observation_id: 'level2_metadata_only_2D2', detector: 'D2', release: 'DR1', timestamp: '2026-09-01T00:00:00Z', wavelength_um: null, bandwidth_um: null, valid_fraction: null, center_ra: null, center_dec: null, size_deg: null }], limited: false, cached: false };
async function select(page: Page, ra: string) {
  await page.getByLabel('Right ascension in degrees').fill(ra);
  await page.getByLabel('Declination in degrees').fill('-20');
  await page.getByRole('button', { name: 'Go to location', exact: true }).click();
}
test.beforeEach(async ({ page }) => {
  await page.route('**/api/health', route => route.fulfill({ json: { offline: true } }));
  await page.route('**/api/objects?*', route => route.fulfill({ json: catalog }));
});

test('metadata retries independently, shows unknown pixel fields honestly, and never requests observation images', async ({ page }) => {
  const images: string[] = [], preparations: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/frames/')) images.push(request.url());
    if (request.method() === 'POST') preparations.push(request.url());
  });
  let calls = 0;
  await page.route('**/api/observation-metadata?*', route => ++calls === 1
    ? route.fulfill({ status: 503, json: { detail: 'IRSA metadata temporarily unavailable' } })
    : route.fulfill({ json: response }));
  await page.goto('/'); await select(page, '10');
  const metadata = page.getByLabel('Sky observation metadata', { exact: true });
  await expect(metadata).toContainText('IRSA metadata temporarily unavailable');
  await metadata.getByRole('button', { name: 'Retry observation metadata' }).click();
  await expect(metadata).toContainText('level2_metadata_only_2D2');
  await expect(metadata.getByText('Image analysis required', { exact: true })).toHaveCount(3);
  await expect(metadata).toContainText('Detector coverage: 1.10–1.62 µm');
  await expect(metadata).toContainText('No observation images were downloaded');
  await expect(metadata).toContainText('NASA IRSA SIA');
  expect(images).toEqual([]); expect(preparations).toEqual([]);
  expect(calls).toBe(2);
});

test('late metadata cannot replace a newer position or reopen a closed popup', async ({ page }) => {
  let release: () => void = () => {}, started: () => void = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  const requestStarted = new Promise<void>(resolve => { started = resolve; });
  await page.route('**/api/observation-metadata?*', async route => {
    if (new URL(route.request().url()).searchParams.get('ra') === '10') {
      started(); await gate;
      try { await route.fulfill({ json: response }); } catch { /* Selection aborted this request. */ }
    } else await route.fulfill({ json: { ...response, observations: [], message: 'New position has no saved observation metadata' } });
  });
  await page.goto('/'); await select(page, '10'); await requestStarted;
  await select(page, '20');
  await expect(page.getByLabel('Sky observation metadata', { exact: true })).toContainText('New position');
  await page.getByRole('button', { name: 'Close object information' }).click(); release();
  await page.waitForTimeout(400);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByText('level2_metadata_only_2D2', { exact: true })).toHaveCount(0);
});
