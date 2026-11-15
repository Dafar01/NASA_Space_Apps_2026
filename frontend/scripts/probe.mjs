import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 850 } });
const report = { errors: [], failed: [], responses: [], checks: {} };
page.on('pageerror', e => report.errors.push(e.message));
page.on('requestfailed', r => report.failed.push({ url: r.url(), error: r.failure() }));
page.on('response', r => {
  if (/SPHEREx/.test(r.url())) report.responses.push({ url: r.url(), status: r.status() });
});
try {
  await page.goto('http://127.0.0.1:5173/compatibility.html');
  await page.waitForFunction(() => window.aladin, { timeout: 60000 });
  await page.waitForTimeout(12000);
  report.checks.initial = await page.evaluate(() => {
    const a = window.aladin;
    const layer = a.getBaseImageLayer();
    const [width, height] = a.getSize();
    const data = a.readCanvas({ top: 0, left: 0, w: width, h: height }).data;
    let lit = 0; let min = 255; let max = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] > 15 || data[i+1] > 15 || data[i+2] > 15) lit++;
      min = Math.min(min, data[i]); max = Math.max(max, data[i]);
    }
    return { center: Array.from(a.getRaDec()), fov: a.getFov(), url: layer.url,
      cubeDepth: layer.cubeDepth, cubeFirstFrame: layer.cubeFirstFrame,
      frequency: layer.getFrequency(), lit, min, max, errors: window.probeErrors };
  });
  const canvas = page.locator('.aladin-catalogCanvas');
  await canvas.click({ position: { x: 540, y: 350 } });
  report.checks.click = await page.evaluate(() => window.lastClick);
  await canvas.hover(); await page.mouse.wheel(0, -400);
  await page.waitForTimeout(1500);
  report.checks.zoom = await page.evaluate(() => window.aladin.getFov());
  await page.mouse.move(600, 400); await page.mouse.down();
  await page.mouse.move(750, 450, { steps: 12 }); await page.mouse.up();
  await page.waitForTimeout(1000);
  report.checks.pan = await page.evaluate(() => window.aladin.getRaDec());
  await mkdir('artifacts', { recursive: true });
  await page.screenshot({ path: 'artifacts/compatibility.png' });
  await page.evaluate(() => {
    const layer = window.aladin.getBaseImageLayer();
    const min = 299792458 / layer.emMax;
    const max = 299792458 / layer.emMin;
    layer.setFrequency({ value: min + (63.5 / layer.cubeDepth) * (max - min), unit: 'Hz' });
  });
  await page.waitForTimeout(3000);
  await mkdir('artifacts', { recursive: true });
} catch (error) { report.errors.push(String(error)); }
await mkdir('artifacts', { recursive: true });
await writeFile('artifacts/compatibility.json', JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
await browser.close();
