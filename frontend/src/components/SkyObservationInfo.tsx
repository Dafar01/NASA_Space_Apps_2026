import { useEffect, useState } from 'react';
import { LoaderCircle, RefreshCw } from 'lucide-react';
import type { SkyPosition } from '../types/astronomy';
import { apiRequest, observationDate } from '../types/observations';
import { BANDS } from '../config/bands';
import { ObservationDetails } from './ObservationDetails';
import type { FrameMetadata } from './ObservationDetails';

interface Record extends FrameMetadata {
  timestamp: string; detector: string; center_ra: number | null; center_dec: number | null; size_deg: number | null;
}
interface Lookup {
  source: 'saved_cutouts' | 'NASA IRSA TAP'; observations: Record[]; limited: boolean;
  retrieved_at: string; cached: boolean; message?: string; cache_warning?: string | null;
}
export function SkyObservationInfo({ position, offline }: { position: SkyPosition; offline: boolean }) {
  const [attempt, setAttempt] = useState(0);
  const [resource, setResource] = useState<{ key: string; data?: Lookup; error?: string }>({ key: '' });
  const [selected, setSelected] = useState(0);
  const ra = Number(position.ra.toFixed(6)), dec = Number(position.dec.toFixed(6));
  const key = `${ra}:${dec}:${offline}:${attempt}`;
  useEffect(() => {
    const controller = new AbortController();
    setResource({ key }); setSelected(0);
    const timer = setTimeout(() => {
      const params = new URLSearchParams({ ra: String(ra), dec: String(dec), cache_only: String(offline) });
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(25000)]);
      void apiRequest<Lookup>(`/api/observation-metadata?${params}`, { signal })
        .then(data => { if (!controller.signal.aborted) setResource({ key, data }); })
        .catch(error => { if (!controller.signal.aborted) setResource({ key, error: signal.aborted ? 'Observation metadata timed out. Please retry.' : error instanceof Error ? error.message : 'Unable to look up observation metadata.' }); });
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [ra, dec, offline, attempt, key]);
  const data = resource.key === key ? resource.data : undefined;
  const error = resource.key === key ? resource.error : undefined;
  const record = data?.observations[selected];
  return <section className="sky-observations" aria-label="Sky observation metadata"><div className="section-heading"><h3>Observation details</h3></div>
    {!data && !error && <p className="object-status" role="status"><LoaderCircle size={14} className="spin" />Looking up observation metadata…</p>}
    {error && <><p className="field-error" role="status">{error}</p><button className="button button-secondary full-width" onClick={() => setAttempt(value => value + 1)}><RefreshCw size={13} />Retry observation metadata</button></>}
    {data && !record && <p className="field-hint">{data.message || 'No spectral observations returned at this position.'}</p>}
    {record && <><label className="object-match-label">{data!.source === 'saved_cutouts' ? 'Saved field observations' : 'Archive observations'}<select aria-label="Observation record" value={selected} onChange={event => setSelected(Number(event.target.value))}>{data!.observations.map((record, index) => <option key={`${record.observation_id}:${record.release}`} value={index}>{observationDate(record.timestamp)} · {record.detector} · {record.release}</option>)}</select></label>
      <ObservationDetails frame={record} />
      {data!.source === 'saved_cutouts' ? <p className="field-hint">Saved cutout centered at {record.center_ra?.toFixed(6)}°, {record.center_dec?.toFixed(6)}°{record.size_deg !== null ? ` · ${(record.size_deg * 60).toFixed(1)}′ field` : ''}. Wavelength is measured at that center; valid pixels describes the field.</p> : <p className="field-hint">Detector coverage: {BANDS.find(band => band.id === record.detector)?.range} µm. Exact wavelength, bandwidth, and valid pixels require image analysis. No observation images were downloaded.</p>}
    </>}
    {data?.limited && <p className="field-hint">Showing a limited sample of up to 24 observations.</p>}
    {data && <p className="field-hint">{data.source === 'saved_cutouts' ? 'Local observation metadata' : 'NASA IRSA TAP'}{data.cached ? ' · saved locally' : ''}</p>}
    {data?.cache_warning && <p className="field-error">{data.cache_warning}</p>}
  </section>;
}
