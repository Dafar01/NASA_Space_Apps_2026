import type { SkyPosition } from './astronomy';

export interface SeriesQuery extends SkyPosition {
  size_deg: number;
  detector: string;
}

export interface ObservationFrame {
  timestamp: string;
  mjd: number;
  wavelength_um: number;
  bandwidth_um: number;
  observation_id: string;
  release: string;
  processing_version: string;
  valid_fraction: number;
  image_url: string;
  image_file: string;
  source_url: string;
}

export interface SavedSeries {
  id: string;
  query: SeriesQuery;
  created_at: string;
  frame_count: number;
  date_start: string | null;
  date_end: string | null;
  wavelength_min_um: number | null;
  wavelength_max_um: number | null;
}

export interface ObservationSeries extends SavedSeries {
  state: 'ready' | 'empty';
  frames: ObservationFrame[];
  notes: string[];
  cached: boolean;
  offline: boolean;
  message: string;
}

export interface PendingSeries {
  id: string;
  state: 'preparing' | 'error';
  query: SeriesQuery;
  message: string;
  completed: number;
  total: number;
}

export type SeriesResponse = ObservationSeries | PendingSeries;

export async function apiRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null;
    throw new Error(typeof detail === 'string' ? detail : `Observation API returned HTTP ${response.status}.`);
  }
  if (!body || typeof body !== 'object') throw new Error('The observation API returned an invalid response.');
  return body as T;
}

export function observationDate(timestamp: string, includeTime = false): string {
  return new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric',
    ...(includeTime ? { hour: '2-digit', minute: '2-digit', second: '2-digit' } as const : {}),
    timeZone: 'UTC', hour12: false }).format(new Date(timestamp));
}
