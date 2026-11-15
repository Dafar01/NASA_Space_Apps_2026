import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizePosition, parsePosition } from '../src/types/astronomy.ts';
import { frequencyForFrame } from '../src/config/surveys.ts';

test('ICRS RA wraps across the meridian without losing declination', () => {
  assert.deepEqual(normalizePosition(-0.25, -90), { ra: 359.75, dec: -90 });
  assert.deepEqual(normalizePosition(720.125, 90), { ra: 0.125, dec: 90 });
  assert.deepEqual(normalizePosition(360, 0), { ra: 0, dec: 0 });
});

test('rejects invalid sky positions and ambiguous manual inputs', () => {
  for (const [ra, dec] of [[NaN, 0], [Infinity, 0], [12, 90.01], [12, -90.01]]) assert.equal(normalizePosition(ra, dec), null);
  for (const [ra, dec] of [['', '0'], [' ', '-2'], ['0x10', '0'], ['12abc', '0'], ['1', 'Infinity'], ['1', '91']]) assert.equal(parsePosition(ra, dec), null);
  assert.deepEqual(parsePosition(' 83.82208 ', '-5.39111'), { ra: 83.82208, dec: -5.39111 });
});

test('compatibility frequency lands in the verified nonblank frame, not the blank default', () => {
  const low = 299792458 / 5.009e-6;
  const high = 299792458 / 0.743e-6;
  const hz = frequencyForFrame(62, 102, 0.743e-6, 5.009e-6);
  assert.equal(Math.floor(102 * (hz - low) / (high - low)), 62);
});
