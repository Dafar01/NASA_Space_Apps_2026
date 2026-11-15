import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

async function fov(page: Page) { return Number.parseFloat((await page.getByTestId('field-of-view').textContent())!); }
async function renderedSkyPixels(page: Page) {
  const screenshot = await page.locator('.map-stage').screenshot();
  return page.evaluate(async base64 => {
    const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const context = canvas.getContext('2d')!; context.drawImage(image, 0, 0);
    const { data } = context.getImageData(Math.floor(image.width * .2), Math.floor(image.height * .25), Math.floor(image.width * .6), Math.floor(image.height * .5));
    let count = 0;
    for (let i = 0; i < data.length; i += 4) {
      const spread = Math.max(data[i], data[i+1], data[i+2]) - Math.min(data[i], data[i+1], data[i+2]);
      if (data[i] > 40 && spread < 18) count++;
    }
    return count;
  }, screenshot.toString('base64'));
}

test('real sky: pointer coordinates, anchored popup, persistent tabs, presets, controls, and mobile', async ({ page }) => {
  // Isolate catalog/archive work while preserving normal HiPS image caching.
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      if (url.origin === location.origin && url.pathname === '/api/series' && init?.method === 'POST') return Response.json({ state: 'empty', frames: [], notes: [] });
      if (url.origin === location.origin && url.pathname === '/api/objects') return Response.json({ candidates: [], radius_arcsec: 30, total_matches: 0, source: 'SIMBAD test transport', retrieved_at: '2026-11-14T00:00:00Z', cached: false, search_limited: false });
      if (url.origin === location.origin && url.pathname === '/api/observation-metadata') return Response.json({ observations: [], source: 'NASA IRSA TAP', limited: false, cached: false });
      return fetch(input, init);
    };
  });
  const errors: string[] = [];
  const tiles: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('response', response => { if (response.url().includes('/SPHEREx/') && /Npix.*\.png$/.test(response.url()) && response.status() === 200) tiles.push(response.url()); });
  await page.goto('/');
  await expect(page).toHaveTitle('Atlas - Sky Viewer');
  await expect(page.getByRole('tab', { name: 'Sky view', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByLabel('Chronological observations')).not.toBeVisible();
  await expect(page.locator('.map-stage')).toHaveAttribute('data-status', 'ready', { timeout: 60000 });
  await expect.poll(() => renderedSkyPixels(page)).toBeGreaterThan(1000);
  expect(tiles.length).toBeGreaterThan(0); expect(tiles.every(url => url.endsWith('_62.png'))).toBeTruthy();
  await expect(page.locator('.aladin-imageCanvas')).toHaveCount(1);
  const map = page.locator('.aladin-catalogCanvas');
  const bounds = (await map.boundingBox())!;
  await map.click({ position: { x: 4, y: 4 } });
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await map.hover({ position: { x: bounds.width * .43, y: bounds.height * .48 } });
  const pointerBefore = await page.getByTestId('pointer-coordinates').textContent();
  await map.hover({ position: { x: bounds.width * .57, y: bounds.height * .48 } });
  await expect(page.getByTestId('pointer-coordinates')).not.toHaveText(pointerBefore!);
  await expect(page.getByTestId('field-of-view')).toHaveText('320.00°');
  await map.click({ position: { x: bounds.width * .57, y: bounds.height * .48 } });
  const popup = page.getByRole('dialog', { name: 'Selected sky object' });
  await expect(popup).toBeVisible();
  const selected = await page.getByTestId('selected-coordinates').textContent();
  const ra = Number.parseFloat((await page.getByTestId('selected-ra').textContent())!);
  expect(ra).toBeGreaterThanOrEqual(0); expect(ra).toBeLessThan(360); expect(ra).not.toBeCloseTo(266.41683, 3);
  const tether = page.locator('.popup-tether circle');
  const anchorBefore = await tether.getAttribute('cx');
  // Drag from a clear area on the left of the card.
  await page.mouse.move(bounds.x + bounds.width * .2, bounds.y + bounds.height * .6);
  await page.mouse.down(); await page.mouse.move(bounds.x + bounds.width * .31, bounds.y + bounds.height * .62, { steps: 12 }); await page.mouse.up();
  await expect(tether).not.toHaveAttribute('cx', anchorBefore!);
  await expect(page.getByTestId('selected-coordinates')).toHaveText(selected!);
  await page.getByRole('button', { name: 'Close object information' }).click();
  await expect(popup).toHaveCount(0);
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  await expect.poll(() => fov(page)).toBeLessThan(320);
  const zoomed = await fov(page);
  await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
  await expect(page.getByTestId('sky-map')).not.toBeVisible();
  await page.getByRole('tab', { name: 'Sky view', exact: true }).click();
  await expect.poll(() => fov(page)).toBeCloseTo(zoomed, 1);
  await expect(page.locator('.aladin-imageCanvas')).toHaveCount(1);
  await page.getByRole('button', { name: 'Zoom out', exact: true }).click();
  await expect.poll(() => fov(page)).toBeGreaterThan(zoomed);
  await expect(page.getByRole('button', { name: 'Coordinate grid' })).toHaveCount(0);
  for (const [target, expectedRa, expectedDec] of [['Galactic Center', '266.416830', '-29.007810'], ['Orion Nebula', '83.822080', '-5.391110'], ['Pinwheel Galaxy', '210.802270', '+54.348950']]) {
    await page.locator('.target').filter({ hasText: target }).click();
    await expect(page.getByTestId('selected-ra')).toHaveText(`${expectedRa}°`);
    await expect(page.getByTestId('selected-dec')).toHaveText(`${expectedDec}°`);
    await expect(page.getByLabel('Observation cutout preview')).toHaveCount(0);
  }
  await page.getByLabel('Right ascension in degrees').fill('720.125');
  await page.getByLabel('Declination in degrees').fill('91');
  await page.getByRole('button', { name: 'Go to location', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Declination');
  await page.getByLabel('Declination in degrees').fill('-12.25');
  await page.getByRole('button', { name: 'Go to location', exact: true }).click();
  await expect(page.getByTestId('selected-ra')).toHaveText('0.125000°');
  await expect(page.getByTestId('selected-dec')).toHaveText('-12.250000°');
  await expect(page.getByTestId('field-of-view')).toHaveText('2.00°');
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.getByRole('button', { name: 'Copy coordinates' }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('0.125000, -12.250000');
  await page.keyboard.press('Escape'); await expect(popup).toHaveCount(0);
  await page.getByRole('button', { name: 'Reset sky view' }).click();
  await expect(page.getByTestId('field-of-view')).toHaveText('320.00°');
  // The camera updates before asynchronous HiPS tiles have finished painting.
  await page.waitForTimeout(1800);
  await page.screenshot({ path: 'artifacts/atlas-sky-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => page.locator('.aladin-imageCanvas').evaluate((canvas: HTMLCanvasElement) => canvas.width)).toBeLessThan(500);
  await page.locator('.target').filter({ hasText: 'Orion Nebula' }).click();
  await expect(page.getByTestId('selected-ra')).toHaveText('83.822080°');
  await expect(popup).toBeVisible();
  const card = (await popup.boundingBox())!; const stage = (await page.locator('.map-stage').boundingBox())!;
  expect(card.x).toBeGreaterThanOrEqual(stage.x); expect(card.x + card.width).toBeLessThanOrEqual(stage.x + stage.width + 1);
  expect(card.height).toBeLessThan(stage.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await popup.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'artifacts/atlas-sky-mobile.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('a preset selected before startup completes navigates when the map becomes ready', async ({ page }) => {
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/health', async route => { await gate; await route.continue(); });
  await page.route('**/api/objects?*', route => route.fulfill({ json: { candidates: [], radius_arcsec: 30, total_matches: 0, source: 'SIMBAD test transport', retrieved_at: '2026-11-14T00:00:00Z', cached: false, search_limited: false } }));
  await page.route('**/api/observation-metadata?*', route => route.fulfill({ json: { observations: [], source: 'NASA IRSA TAP', limited: false, cached: false } }));
  await page.goto('/');
  await page.locator('.target').filter({ hasText: 'Orion Nebula' }).click();
  await expect(page.getByTestId('sky-map')).toHaveCount(0);
  release();
  await expect(page.locator('.map-stage')).toHaveAttribute('data-status', 'ready', { timeout: 60000 });
  await expect(page.getByTestId('field-of-view')).toHaveText('8.00°');
  await expect(page.getByTestId('selected-ra')).toHaveText('83.822080°');
  await expect(page.getByLabel('Observation cutout preview')).toHaveCount(0);
});

test('metadata network failure is explicit and retry recovers without an unrelated survey', async ({ page }) => {
  const otherSurveys: string[] = [];
  page.on('request', request => { if (/DSS|2MASS|WISE/.test(request.url())) otherSurveys.push(request.url()); });
  const metadata = '**/SPHEREx/spherex_allsky_cube/properties';
  await page.route(metadata, route => route.abort('internetdisconnected'));
  await page.goto('/');
  await expect(page.getByText('SPHEREx network failure', { exact: true }).first()).toBeVisible();
  await page.unroute(metadata); await page.getByRole('button', { name: 'Retry SPHEREx' }).click();
  await expect(page.locator('.map-stage')).toHaveAttribute('data-status', 'ready', { timeout: 60000 });
  await expect(page.locator('.aladin-imageCanvas')).toHaveCount(1);
  expect(otherSurveys).toEqual([]);
});

test('a missing tile is described as missing imagery, not an empty sky', async ({ page }) => {
  await page.route('**/Norder0/Dir0/Npix0_62.png', route => route.fulfill({ status: 404, body: 'Missing' }));
  await page.goto('/');
  await expect(page.getByText('Missing SPHEREx imagery', { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/A missing tile is not an empty sky region/)).toBeVisible();
});
