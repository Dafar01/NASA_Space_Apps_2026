export const SPHEREX = {
  url: 'https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube',
  name: 'SPHEREx DR1 all-sky cube',
  frame: 62, // Zero-based, nominal 3.293 µm, per NASA's wavelength array.
  wavelength: 3.293,
  documentation: 'https://irsa.ipac.caltech.edu/data/SPHEREx/docs/overview_dr1.html',
} as const;

export const INITIAL_VIEW = { ra: 266.41683, dec: -29.00781, fov: 320 };

// Aladin treats cubes as uniformly sampled in frequency; NASA's actual
// wavelengths are nonuniform and ascend in the opposite direction. Use the
// documented frequency setter to address the verified tile index, never as
// a physical wavelength conversion. The label comes from NASA's metadata.
export function frequencyForFrame(frame: number, depth: number, emMin: number, emMax: number): number {
  const minHz = 299792458 / emMax;
  const maxHz = 299792458 / emMin;
  return minHz + ((frame + 0.5) / depth) * (maxHz - minHz);
}

export class SurveyError extends Error {
  kind: 'network' | 'missing' | 'compatibility';
  constructor(kind: 'network' | 'missing' | 'compatibility', message: string) {
    super(message);
    this.kind = kind;
  }
}

export async function checkTile(url: string, signal: AbortSignal): Promise<void> {
  let response: Response;
  try {
    response = await fetch(url, { signal, cache: 'force-cache' });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new SurveyError('network', 'Could not reach the SPHEREx tile server. Check your connection and browser CORS errors.');
  }
  if (!response.ok) {
    throw new SurveyError(response.status === 404 ? 'missing' : 'network',
      `SPHEREx imagery request returned HTTP ${response.status}. A missing tile is not an empty sky region.`);
  }
  try {
    const bitmap = await createImageBitmap(await response.blob());
    if (bitmap.width < 1 || bitmap.height < 1) throw new Error('Empty image');
    bitmap.close();
  } catch {
    if (signal.aborted) throw signal.reason;
    throw new SurveyError('compatibility', 'The server responded, but its PNG tile could not be decoded.');
  }
}

export async function validateSurvey(signal: AbortSignal): Promise<void> {
  let response: Response;
  try { response = await fetch(`${SPHEREX.url}/properties`, { signal }); }
  catch (error) {
    if (signal.aborted) throw error;
    throw new SurveyError('network', 'Cannot reach NASA IRSA. Check your connection, firewall, or browser CORS errors.');
  }
  if (!response.ok) throw new SurveyError(response.status === 404 ? 'missing' : 'network', `NASA HiPS metadata returned HTTP ${response.status}.`);
  const properties = Object.fromEntries((await response.text()).split(/\r?\n/)
    .filter(line => line.trim() && !line.trim().startsWith('#') && line.includes('='))
    .map(line => { const split = line.indexOf('='); return [line.slice(0, split).trim(), line.slice(split + 1).trim()]; }));
  const wavelengths = properties.data_cube_crval3_arr?.split(/\s+/).map(Number);
  if (properties.dataproduct_type !== 'cube' || Number(properties.hips_cube_depth) !== 102 ||
    !properties.hips_tile_format?.includes('png') || wavelengths?.[SPHEREX.frame] !== SPHEREX.wavelength) {
    throw new SurveyError('compatibility', 'NASA HiPS metadata has changed. The fixed-frame configuration needs to be revalidated.');
  }
  await checkTile(`${SPHEREX.url}/Norder0/Dir0/Npix0_${SPHEREX.frame}.png`, signal);
}
