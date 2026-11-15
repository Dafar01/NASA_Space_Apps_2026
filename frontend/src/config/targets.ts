import type { SkyTarget } from '../types/astronomy';

// CDS Sesame / SIMBAD resolved 2026-11-14. Four stellar positions were
// propagated from catalog J2000 to epoch 2026.0 with published proper motions
// (Astropy apply_space_motion; no parallax correction). These are navigation
// positions, not measured positions from the SPHEREx frames.
// https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?Barnard%27s%20Star
// https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?eps%20Ind%20B
// https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?RW%20Aur
// https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?R%20Dra
export const TARGETS: SkyTarget[] = [
  { name: 'Barnard’s Star', description: 'Fast-moving star · position at 2026.0', ra: 269.446268, dec: 4.768204, fov: 0.5 },
  { name: 'Epsilon Indi B', description: 'Brown dwarf pair · position at 2026.0', ra: 331.096669, dec: -56.800649, fov: 0.5 },
  // Coordinates supplied in the challenge brief; its reference epoch was not specified.
  { name: 'WISE 0855', description: 'Fast-moving brown dwarf · brief coordinates', ra: 133.79512916666664, dec: -7.245147222222222, fov: 0.5 },
  { name: 'RW Aurigae', description: 'Variable star · position at 2026.0', ra: 76.956587, dec: 30.401291, fov: 0.5 },
  { name: 'R Draconis', description: 'Mira variable · position at 2026.0', ra: 248.167233, dec: 66.755024, fov: 0.5 },
  { name: 'Galactic Center', description: 'The heart of the Milky Way', ra: 266.41683, dec: -29.00781, fov: 20 },
  { name: 'Orion Nebula', description: 'A nearby stellar nursery', ra: 83.82208, dec: -5.39111, fov: 8 },
  { name: 'M101 · Pinwheel Galaxy', description: 'A spiral in Ursa Major', ra: 210.80227, dec: 54.34895, fov: 3 },
];

export function targetName(ra: number, dec: number): string {
  return TARGETS.find(target => Math.abs(target.ra - ra) < 0.000002 && Math.abs(target.dec - dec) < 0.000002)?.name
    ?? `RA ${ra.toFixed(4)}° / Dec ${dec.toFixed(4)}°`;
}
