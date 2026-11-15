import { expect, test } from '@playwright/test';
import { OFFLINE_URL } from '../servers';

async function expectBoundedPage(page: import('@playwright/test').Page) {
  expect(await page.evaluate(() => ({ width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight, viewportWidth: innerWidth, viewportHeight: innerHeight })))
    .toEqual(expect.objectContaining({ width: await page.evaluate(() => innerWidth), height: await page.evaluate(() => innerHeight) }));
}

test('branded workspace fits the viewport, scrolls panels, shows one metadata row and loops local audio', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(OFFLINE_URL);
  const logo = page.getByRole('img', { name: 'Atlas', exact: true });
  await expect(logo).toBeVisible();
  expect(await logo.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth === 2481)).toBeTruthy();
  await expectBoundedPage(page);
  await page.locator('.target').filter({ hasText: 'Barnard' }).click();
  await expect(page.getByRole('button', { name: 'Mute ambience', exact: true })).toBeVisible();
  expect(await page.locator('audio').evaluate((audio: HTMLAudioElement) => ({ loop: audio.loop, paused: audio.paused, volume: audio.volume }))).toEqual({ loop: true, paused: false, volume: .18 });
  await page.getByRole('button', { name: 'Mute ambience', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Play ambience', exact: true })).toHaveAttribute('aria-pressed', 'false');
  expect(await page.locator('audio').evaluate((audio: HTMLAudioElement) => audio.paused)).toBeTruthy();
  await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
  await expect(page.getByRole('slider', { name: 'Observation timeline' })).toBeEnabled();
  await expect(page.getByText('Source and processing notes', { exact: true })).toHaveCount(0);
  const details = page.getByRole('region', { name: 'Observation details', exact: true });
  expect(await details.locator('dl > div').evaluateAll(elements => new Set(elements.map(element => element.getBoundingClientRect().top)).size)).toBe(1);
  const play = page.getByRole('button', { name: 'Play observations', exact: true });
  await play.hover(); await expect(play).toHaveCSS('background-color', 'rgb(242, 204, 131)');
  await expectBoundedPage(page);
  for (const viewport of [{ width: 390, height: 844 }, { width: 320, height: 568 }]) {
    await page.setViewportSize(viewport);
    await expectBoundedPage(page);
    await expect(play).toBeInViewport();
    await expect(page.getByRole('slider', { name: 'Observation timeline' })).toBeInViewport();
    expect(await page.locator('.saved-library').evaluate(element => element.scrollHeight > element.clientHeight && getComputedStyle(element).overflowY === 'auto')).toBeTruthy();
    await page.locator('.saved-object-toggle').filter({ hasText: 'Orion Nebula' }).click();
    await expect(page.locator('.playback-heading')).toContainText('Orion Nebula');
    await expectBoundedPage(page);
    await page.getByRole('tab', { name: 'Sky view', exact: true }).click();
    expect(await page.locator('.location-panel').evaluate(element => element.scrollHeight > element.clientHeight)).toBeTruthy();
    await page.locator('.target').filter({ hasText: 'Barnard' }).click();
    await expect(page.getByRole('dialog')).toBeInViewport();
    await expectBoundedPage(page);
    await page.getByRole('tab', { name: 'Observation playback', exact: true }).click();
    await expect(page.getByRole('slider', { name: 'Observation timeline' })).toBeEnabled();
  }
  expect(errors).toEqual([]);
});

test('uncached sky playback reports progress and archive failures, then retries successfully', async ({ page }) => {
  const id = 'ab'.repeat(12);
  const query = { ra: 69.0008, dec: 16.5291, detector: 'D2', size_deg: .1 };
  let posts = 0, polls = 0;
  await page.route('**/api/series?*', route => {
    posts++;
    return route.fulfill({ json: { id, query, state: 'preparing', message: 'Searching NASA IRSA for dated observations…', completed: 0, total: 0 } });
  });
  await page.route(`**/api/series/${id}`, async route => {
    polls++;
    if (posts === 1) return route.fulfill({ json: { id, query, state: 'error', message: 'NASA IRSA observation search timed out. Retry, or open a saved object.' } });
    const library = await page.request.get(`${OFFLINE_URL}/api/series`);
    const saved = (await library.json()).series[0];
    const result = await page.request.get(`${OFFLINE_URL}/api/series/${saved.id}`);
    return route.fulfill({ json: await result.json() });
  });
  await page.goto(OFFLINE_URL);
  await page.getByLabel('Right ascension in degrees').fill(String(query.ra));
  await page.getByLabel('Declination in degrees').fill(String(query.dec));
  await page.getByRole('button', { name: 'Go to location', exact: true }).click();
  expect(posts).toBe(0);
  await page.getByRole('button', { name: 'View observations', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Searching NASA IRSA');
  await expect(page.locator('.loading-elapsed')).toContainText('elapsed');
  await expect(page.locator('.error-banner')).toContainText('timed out');
  await page.locator('.error-banner').getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.locator('.observation-image')).toBeVisible();
  await expect(page.locator('.error-banner')).toHaveCount(0);
  expect(posts).toBe(2); expect(polls).toBe(2);
});

test('a hung backend request times out visibly instead of leaving playback spinning', async ({ page }) => {
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (init?.method === 'POST' && String(input).includes('/api/series')) return new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(init.signal!.reason), { once: true });
      });
      return fetch(input, init);
    };
  });
  await page.goto(OFFLINE_URL);
  await page.getByLabel('Right ascension in degrees').fill('69.0008');
  await page.getByLabel('Declination in degrees').fill('16.5291');
  await page.getByRole('button', { name: 'Go to location', exact: true }).click();
  await page.getByRole('button', { name: 'View observations', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Preparing your view' })).toBeVisible();
  await expect(page.locator('.error-banner')).toContainText('25 seconds');
  await expect(page.getByRole('heading', { name: 'Preparing your view' })).toHaveCount(0);
});
