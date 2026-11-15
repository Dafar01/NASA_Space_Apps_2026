export interface SkyPosition {
  ra: number;
  dec: number;
}

export interface SkyTarget extends SkyPosition {
  name: string;
  description: string;
  fov: number;
}

export type ViewerStatus = {
  phase: 'initializing' | 'loading' | 'ready' | 'error' | 'offline';
  message: string;
  detail?: string;
};

export function normalizePosition(ra: number, dec: number): SkyPosition | null {
  if (!Number.isFinite(ra) || !Number.isFinite(dec) || dec < -90 || dec > 90) return null;
  const remainder = ra % 360;
  const wrapped = remainder < 0 ? remainder + 360 : remainder;
  return { ra: wrapped === 360 || Object.is(wrapped, -0) ? 0 : wrapped, dec };
}

export function parsePosition(ra: string, dec: string): SkyPosition | null {
  if (!ra.trim() || !dec.trim()) return null;
  // Decimal degrees only; reject hexadecimal, trailing text, and infinities.
  const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
  if (!decimal.test(ra.trim()) || !decimal.test(dec.trim())) return null;
  return normalizePosition(Number(ra), Number(dec));
}

export function formatDegrees(value: number, signed = false): string {
  return `${signed && value >= 0 ? '+' : ''}${value.toFixed(6)}°`;
}
