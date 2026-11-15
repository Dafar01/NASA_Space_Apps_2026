import test from 'node:test';
import assert from 'node:assert/strict';
import { groupSavedObjects, samePosition, wavelengthLabel } from '../src/config/bands.ts';
import type { SavedSeries } from '../src/types/observations.ts';

function sequence(id: string, detector: string, size = .1, ra = 269.446268): SavedSeries {
  return { id, query: { ra, dec: 4.768204, detector, size_deg: size }, created_at: '', frame_count: 4,
    date_start: null, date_end: null, wavelength_min_um: null, wavelength_max_um: null };
}
test('one object contains ordered, unique bands and prefers the standard field without deleting alternatives', () => {
  const library = [sequence('wide', 'D2', .2), sequence('six', 'D6'), sequence('two', 'D2'), sequence('one', 'D1', .05), sequence('other', 'D2', .1, 100)];
  const groups = groupSavedObjects(library);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].name, 'Barnard’s Star');
  assert.deepEqual(groups[0].bands.map(band => band.id), ['one', 'two', 'six']);
  assert.equal(groups[0].bands[0].query.size_deg, .05);
  assert.equal(library.length, 5);
});
test('band labels use actual observed wavelength ranges when available', () => {
  const saved = sequence('two', 'D2');
  assert.equal(wavelengthLabel(saved), '1.10–1.62 µm');
  saved.wavelength_min_um = 1.4957; saved.wavelength_max_um = 1.5172;
  assert.equal(wavelengthLabel(saved), '1.496–1.517 µm');
});
test('selection matching tolerates rounding and rejects other positions', () => {
  assert.equal(samePosition({ ra: 133.7951291667, dec: -7.2451472222 }, { ra: 133.795129, dec: -7.245147 }), true);
  assert.equal(samePosition({ ra: 10, dec: 20 }, { ra: 10.01, dec: 20 }), false);
  assert.equal(samePosition({ ra: 10, dec: 20 }, null), false);
});
