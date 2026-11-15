import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { ObservationSeries } from '../../src/types/observations';
import { observationDate } from '../../src/types/observations';
import { TARGETS, targetName } from '../../src/config/targets';
import { OFFLINE_URL } from '../servers';

const directory = fileURLToPath(new URL('../../../data/cache/series', import.meta.url));
const sequences: ObservationSeries[] = existsSync(directory) ? readdirSync(directory).flatMap(id => {
  const path = `${directory}/${id}/manifest.json`;
  return existsSync(path) ? [JSON.parse(readFileSync(path, 'utf8')) as ObservationSeries] : [];
}) : [];
const saved = sequences.find(sequence => sequence.frames.length >= 3 && sequence.query.detector === 'D2' && sequence.query.ra === TARGETS[0].ra && sequence.query.size_deg === .1);
async function blockExternal(page: Page, external: string[]) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) { external.push(url.href); return route.abort('internetdisconnected'); }
    return route.continue();
  });
}

test('sky presets request metadata only; explicit playback, slider, wraparound, tabs and mobile work locally', async ({ page }) => {
  test.skip(!saved, 'Prepare Barnard’s Star D2 using backend.prepare_demo before this integration test.');
  const sequence = saved!;
  const external: string[] = []; const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const imageRequests: string[] = [];
  const preparationRequests: string[] = [];
  page.on('request', request => {
    if (/\/api\/series\/.+\/frames\//.test(request.url())) imageRequests.push(request.url());
    if (request.method() === 'POST' && request.url().includes('/api/series')) preparationRequests.push(request.url());
  });
  await blockExternal(page, external);
  await page.goto(OFFLINE_URL);
  await expect(page.getByRole('tab', { name: 'Sky view', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByLabel('Chronological observations')).not.toBeVisible();
  await page.locator('.target').filter({ hasText: TARGETS[0].name }).click();
  await expect(page.getByLabel('Observation cutout preview')).toHaveCount(0);
  const metadata = page.getByLabel('Sky observation metadata', { exact: true });
  await expect(metadata.getByLabel('Observation record', { exact: true })).toBeVisible();
  await expect(metadata).toContainText('Saved cutout centered at');
  expect(imageRequests).toEqual([]); expect(preparationRequests).toEqual([]);
  await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
  const slider = page.getByRole('slider', { name: 'Observation timeline' });
  await expect(slider).toBeEnabled(); await expect(slider).toHaveValue('0');
  await expect(page.getByTestId('observation-date')).toHaveText(`${observationDate(sequence.frames[0].timestamp, true)} UTC`);
  const details = page.getByRole('region', { name: 'Observation details', exact: true });
  await expect(details).toContainText(sequence.frames[0].observation_id);
  expect((await details.boundingBox())!.y).toBeLessThan((await page.locator('.observation-stage').boundingBox())!.y);
  await expect(page.getByLabel('Infrared wavelength', { exact: true })).toHaveCount(0);
  await expect(page.locator('.loop-indicator')).toHaveCount(0);
  // The collection has exactly one card per object, with wavelength choices inside.
  const name = targetName(sequence.query.ra, sequence.query.dec);
  const object = page.locator('.saved-object-toggle').filter({ hasText: name });
  await expect(object).toHaveCount(1); await expect(object).toHaveAttribute('aria-expanded', 'true');
  const bands = page.getByRole('group', { name: `${name} wavelengths`, exact: true });
  await expect(bands.getByRole('button')).toHaveCount(new Set(sequences.filter(s => s.query.ra === sequence.query.ra && s.query.dec === sequence.query.dec).map(s => s.query.detector)).size);
  await page.getByRole('button', { name: 'Previous observation', exact: true }).click();
  await expect(slider).toHaveValue(String(sequence.frame_count - 1));
  await page.getByRole('button', { name: 'Next observation', exact: true }).click(); await expect(slider).toHaveValue('0');
  await page.getByRole('button', { name: 'Next observation', exact: true }).click(); await expect(slider).toHaveValue('1');
  await expect(page.locator('.observation-image')).toHaveAttribute('src', sequence.frames[1].image_url);
  await slider.focus(); await slider.press('End');
  await page.getByLabel('Playback speed').selectOption('4');
  await page.getByRole('button', { name: 'Play observations', exact: true }).click();
  await expect.poll(() => slider.inputValue()).not.toBe(String(sequence.frame_count - 1));
  // Longer than one complete cycle: playback must still be running.
  await page.waitForTimeout(sequence.frame_count * 250 + 350);
  await expect(page.getByRole('button', { name: 'Pause playback', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Pause playback', exact: true }).click();
  const paused = await slider.inputValue(); await page.waitForTimeout(600); await expect(slider).toHaveValue(paused);
  await page.getByRole('button', { name: 'Play observations', exact: true }).click();
  await page.getByRole('tab', { name: 'Sky view', exact: true }).click();
  await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Play observations', exact: true })).toBeVisible();
  // Another wavelength is an independent real sequence under the same object.
  const alternate = sequences.find(s => s.query.ra === sequence.query.ra && s.query.dec === sequence.query.dec && s.query.detector !== 'D2' && s.query.size_deg === .1 && s.frames.length > 1);
  if (alternate) {
    await bands.locator('button').filter({ hasText: alternate.query.detector }).click();
    await expect(page.locator('.observation-image')).toHaveAttribute('src', alternate.frames[0].image_url);
    await expect(details).toContainText(alternate.frames[0].observation_id);
  }
  await page.screenshot({ path: 'artifacts/atlas-playback-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: 'artifacts/atlas-playback-mobile.png', fullPage: true });
  // Cold reload retrieves local manifests/images, with the sky as landing tab.
  await page.reload();
  await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
  await object.click(); await expect(slider).toBeEnabled();
  await expect(slider).toHaveValue('0');
  await expect(page.getByRole('button', { name: /fetch.*save/i })).toHaveCount(0);
  await expect(page.getByRole('checkbox', { name: 'Offline demo' })).toHaveCount(0);
  expect(external).toEqual([]); expect(errors).toEqual([]);
});

test('a delayed saved response cannot replace a newer coordinate selection', async ({ page }) => {
  test.skip(!saved, 'Requires a real cached sequence.');
  const sequence = saved!;
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  let started: () => void = () => {};
  const requestStarted = new Promise<void>(resolve => { started = resolve; });
  await page.route(`**/api/series/${sequence.id}`, async route => {
    const response = await route.fetch(); started(); await gate;
    try { await route.fulfill({ response }); } catch { /* Intentionally canceled. */ }
  });
  await page.goto(OFFLINE_URL);
  await page.locator('.target').filter({ hasText: TARGETS[0].name }).click();
  await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
  await requestStarted;
  await page.getByRole('tab', { name: 'Sky view', exact: true }).click();
  await page.getByLabel('Right ascension in degrees').fill('12.345');
  await page.getByLabel('Declination in degrees').fill('67.89');
  await page.getByRole('button', { name: 'Go to location', exact: true }).click();
  release();
  await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('not saved locally');
  await expect(page.getByRole('slider', { name: 'Observation timeline' })).toBeDisabled();
  await expect(page.locator('.observation-image')).toHaveCount(0);
  await expect(page.locator('.playback-heading h2')).toHaveText(targetName(12.345, 67.89));
});

test('compiled app on a fresh offline backend needs no external requests or browser cache', async ({ page }) => {
  test.skip(!saved, 'Requires a real cached sequence and npm run build.');
  const external: string[] = []; await blockExternal(page, external);
  await page.goto(OFFLINE_URL);
  await expect(page.getByText('The full-sky map needs a connection', { exact: true }).first()).toBeVisible();
  await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
  await page.locator('.saved-object-toggle').filter({ hasText: targetName(saved!.query.ra, saved!.query.dec) }).click();
  const slider = page.getByRole('slider', { name: 'Observation timeline' });
  await expect(slider).toBeEnabled();
  await page.getByRole('button', { name: 'Next observation', exact: true }).click();
  await expect(slider).toHaveValue('1'); expect(external).toEqual([]);
});

test('rebuilt previews expose available pixels and reserve checkerboard for genuinely absent data', async ({ page }) => {
  const sequence = sequences.find(sequence => Math.abs(sequence.query.ra - 83.82208) < .000002 && sequence.frames[0]?.available_fraction != null);
  test.skip(!sequence, 'Requires a locally rebuilt Orion sequence.');
  const frame = sequence!.frames[0];
  const external: string[] = []; await blockExternal(page, external);
  await page.goto(OFFLINE_URL);
  await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
  await page.locator('.saved-object-toggle').filter({ hasText: targetName(sequence!.query.ra, sequence!.query.dec) }).click();
  const image = page.locator('.observation-image');
  await expect(image).toHaveAttribute('src', frame.image_url);
  const details = page.getByRole('region', { name: 'Observation details', exact: true });
  await expect(details).toContainText('Available pixels');
  await expect(details).toContainText(`${(frame.available_fraction! * 100).toFixed(1)}%`);
  await expect(details).toContainText('Quality-checked pixels');
  const pixels = await image.evaluate(async (element: HTMLImageElement) => {
    await element.decode();
    const canvas = document.createElement('canvas'); canvas.width = element.naturalWidth; canvas.height = element.naturalHeight;
    const context = canvas.getContext('2d')!; context.drawImage(element, 0, 0);
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let available = 0, missing = 0, unexpected = 0;
    for (let index = 0; index < data.length; index += 4) {
      const r = data[index], g = data[index + 1], b = data[index + 2];
      if (r === g && g === b) available++;
      else if ((r === 38 && g === 49 && b === 61) || (r === 58 && g === 70 && b === 82)) missing++;
      else unexpected++;
    }
    return { available, missing, unexpected, total: canvas.width * canvas.height };
  });
  expect(pixels.available).toBe(Math.round(frame.available_fraction! * pixels.total));
  expect(pixels.missing).toBe(pixels.total - pixels.available);
  expect(pixels.unexpected).toBe(0);
  expect(frame.available_fraction!).toBeGreaterThan(frame.valid_fraction);
  await page.screenshot({ path: 'artifacts/atlas-orion-restored-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: 'artifacts/atlas-orion-restored-mobile.png', fullPage: true });
  expect(external).toEqual([]);
});

test('the previously failing sky coordinates open real SIA-discovered frames and scrub offline', async ({ page }) => {
  const sequence = sequences.find(sequence => sequence.query.ra === 258.661698 && sequence.query.dec === 14.390973 && sequence.query.detector === 'D2');
  test.skip(!sequence || sequence.frame_count < 2, 'Requires the live SIA regression sequence.');
  const external: string[] = []; await blockExternal(page, external);
  await page.goto(OFFLINE_URL);
  await page.getByLabel('Right ascension in degrees').fill('258.661698');
  await page.getByLabel('Declination in degrees').fill('14.390973');
  await page.getByRole('button', { name: 'Go to location', exact: true }).click();
  await page.getByRole('button', { name: 'View observations', exact: true }).click();
  const slider = page.getByRole('slider', { name: 'Observation timeline' });
  await expect(slider).toBeEnabled();
  await expect(page.locator('.observation-image')).toHaveAttribute('src', sequence!.frames[0].image_url);
  await expect(page.getByTestId('observation-date')).toHaveText(`${observationDate(sequence!.frames[0].timestamp, true)} UTC`);
  await slider.focus(); await slider.press('End');
  await expect(slider).toHaveValue(String(sequence!.frame_count - 1));
  await expect(page.locator('.observation-image')).toHaveAttribute('src', sequence!.frames.at(-1)!.image_url);
  await page.getByRole('button', { name: 'Next observation', exact: true }).click();
  await expect(slider).toHaveValue('0');
  await expect(page.locator('.error-banner')).toHaveCount(0);
  await page.screenshot({ path: 'artifacts/atlas-sia-playback.png', fullPage: true });
  expect(external).toEqual([]);
});

test('a failed observation-service connection is visible and retry resumes the selected preset', async ({ page }) => {
  test.skip(!saved, 'Requires a real cached Barnard’s Star sequence.');
  let calls = 0;
  let healthy = false;
  await page.route('**/api/health', route => {
    calls++;
    return healthy ? route.fulfill({ json: { offline: true } })
      : route.fulfill({ status: 503, json: { detail: 'Service starting' } });
  });
  await page.route('**/SPHEREx/**', route => route.abort('internetdisconnected'));
  await page.route('**/api/observation-metadata?*', route => route.fulfill({ json: { observations: [], source: 'NASA IRSA TAP', limited: false, cached: false } }));
  await page.goto('/');
  await page.locator('.target').filter({ hasText: TARGETS[0].name }).click();
  await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
  const banner = page.locator('.error-banner');
  await expect(banner).toContainText('Observation service unavailable');
  healthy = true;
  await banner.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByRole('slider', { name: 'Observation timeline' })).toBeEnabled();
  await expect(banner).toHaveCount(0);
  expect(calls).toBeGreaterThanOrEqual(2);
});
