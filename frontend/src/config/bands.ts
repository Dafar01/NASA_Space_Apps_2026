import type { SkyPosition } from '../types/astronomy';
import type { SavedSeries } from '../types/observations';
import { targetName } from './targets.ts';

export const BANDS = [
  { id: 'D1', range: '0.75–1.09' }, { id: 'D2', range: '1.10–1.62' },
  { id: 'D3', range: '1.63–2.41' }, { id: 'D4', range: '2.42–3.82' },
  { id: 'D5', range: '3.83–4.41' }, { id: 'D6', range: '4.42–5.00' },
] as const;
export const DEFAULT_PATCH_SIZE = 0.1;

export function samePosition(a: SkyPosition, b: SkyPosition | null): boolean {
  return b !== null && Math.abs(a.ra - b.ra) < 0.000002 && Math.abs(a.dec - b.dec) < 0.000002;
}

export function wavelengthLabel(saved: SavedSeries): string {
  return saved.wavelength_min_um !== null && saved.wavelength_max_um !== null
    ? `${saved.wavelength_min_um.toFixed(3)}–${saved.wavelength_max_um.toFixed(3)} µm`
    : `${BANDS.find(band => band.id === saved.query.detector)?.range ?? saved.query.detector} µm`;
}

export interface SavedObject { key: string; name: string; position: SkyPosition; bands: SavedSeries[] }

export function groupSavedObjects(library: SavedSeries[]): SavedObject[] {
  const objects = new Map<string, SavedObject>();
  for (const saved of library) {
    const key = `${saved.query.ra.toFixed(6)},${saved.query.dec.toFixed(6)}`;
    let object = objects.get(key);
    if (!object) {
      object = { key, name: targetName(saved.query.ra, saved.query.dec), position: saved.query, bands: [] };
      objects.set(key, object);
    }
    const index = object.bands.findIndex(band => band.query.detector === saved.query.detector);
    // One entry per wavelength band. Prefer the standard field; preserve and
    // use a previously saved nonstandard field when it is the only one.
    if (index < 0) object.bands.push(saved);
    else if (Math.abs(saved.query.size_deg - DEFAULT_PATCH_SIZE) < Math.abs(object.bands[index].query.size_deg - DEFAULT_PATCH_SIZE)) object.bands[index] = saved;
  }
  return [...objects.values()].map(object => ({ ...object, bands: object.bands.sort((a, b) => a.query.detector.localeCompare(b.query.detector)) }));
}
