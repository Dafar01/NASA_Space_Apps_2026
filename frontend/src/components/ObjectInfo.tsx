import { useEffect, useState } from 'react';
import { ExternalLink, Info, LoaderCircle, RefreshCw } from 'lucide-react';
import type { SkyPosition } from '../types/astronomy';
import type { ObjectLookup } from '../types/objects';
import { apiRequest } from '../types/observations';

interface Props { position: SkyPosition | null; offline: boolean }

export function ObjectInfo({ position, offline }: Props) {
  const [resource, setResource] = useState<{ key: string; data?: ObjectLookup; error?: string }>({ key: '' });
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState(0);
  const ra = position ? Number(position.ra.toFixed(6)) : null;
  const dec = position ? Number(position.dec.toFixed(6)) : null;
  const key = `${ra}:${dec}:${offline}:${attempt}`;

  useEffect(() => {
    if (ra === null || dec === null) return;
    const controller = new AbortController();
    setResource({ key });
    setSelected(0);
    // Avoid duplicate StrictMode lookups and unnecessary requests while the
    // user changes targets. Cleanup prevents old responses replacing a click.
    const timer = setTimeout(() => {
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(50000)]);
      const params = new URLSearchParams({ ra: String(ra), dec: String(dec), cache_only: String(offline) });
      void apiRequest<ObjectLookup>(`/api/objects?${params}`, { signal })
        .then(data => { if (!controller.signal.aborted) setResource({ key, data }); })
        .catch(error => {
          if (!controller.signal.aborted) setResource({ key, error: signal.aborted ? 'Object lookup timed out. Please retry.' : error instanceof Error ? error.message : 'Object lookup failed. Please retry.' });
        });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [ra, dec, offline, attempt, key]);

  if (!position) return null;
  const data = resource.key === key ? resource.data : undefined;
  const error = resource.key === key ? resource.error : undefined;
  const candidate = data?.candidates[selected];

  return <section className="panel-section object-info" aria-label="Object information">
    <div className="section-heading"><h3>Object information</h3><Info size={14} /></div>
    {!data && !error && <p className="object-status" role="status"><LoaderCircle className="spin" size={14} />{offline ? 'Checking saved object information…' : 'Looking up nearby catalog objects…'}</p>}
    {error && <><p className="object-status object-error" role="status">{error}</p><button className="button button-secondary full-width" onClick={() => setAttempt(value => value + 1)}><RefreshCw size={13} />Retry object lookup</button></>}
    {data && <>
      {data.candidates.length === 0 ? <p className="object-status" role="status">No catalog match within {data.radius_arcsec} arcseconds of this position. Zoom in and click closer to a source. This does not mean the sky is empty.</p> : <>
        {data.candidates.length > 1 && <label className="object-match-label">Nearby catalog matches<select aria-label="Catalog match" value={selected} onChange={event => setSelected(Number(event.target.value))}>{data.candidates.map((match, index) => <option key={match.name} value={index}>{match.name} · {match.separation_arcsec.toFixed(1)}″</option>)}</select></label>}
        {candidate && <article>
          <h4 data-testid="object-name">{candidate.name}</h4>
          <p className="object-kind" data-testid="object-type">{candidate.object_type}</p>
          <p className="field-hint">Possible catalog match · {candidate.separation_arcsec.toFixed(1)}″ from selection</p>
          <dl className="object-properties">
            <div><dt>Spectral type</dt><dd>{candidate.spectral_type || 'Not cataloged'}</dd></div>
            <div><dt>V magnitude</dt><dd>{candidate.v_magnitude?.toFixed(2) ?? 'Not cataloged'}</dd></div>
            <div><dt>Parallax</dt><dd>{candidate.parallax_mas === null ? 'Not cataloged' : `${candidate.parallax_mas.toFixed(2)} mas`}</dd></div>
            <div><dt>Proper motion RA</dt><dd>{candidate.pm_ra_mas_yr === null ? 'Not cataloged' : `${candidate.pm_ra_mas_yr.toFixed(1)} mas/yr`}</dd></div>
            <div><dt>Proper motion Dec</dt><dd>{candidate.pm_dec_mas_yr === null ? 'Not cataloged' : `${candidate.pm_dec_mas_yr.toFixed(1)} mas/yr`}</dd></div>
            <div><dt>Catalog RA (J2000)</dt><dd>{candidate.ra.toFixed(6)}°</dd></div>
            <div><dt>Catalog Dec (J2000)</dt><dd>{candidate.dec.toFixed(6)}°</dd></div>
          </dl>
          <p className="field-hint">{candidate.match_basis}. Catalog entries can share a position; these are not confirmed individual detections in this image.</p>
          <a className="object-source" href={candidate.source_url} target="_blank" rel="noreferrer">View SIMBAD record<ExternalLink size={12} /></a>
        </article>}
        {data.total_matches > data.candidates.length && <p className="field-hint">Showing {data.candidates.length} of {data.total_matches} nearby matches.</p>}
      </>}
      {data.search_limited && <p className="object-error field-hint">Catalog search reached its result limit. Some matches may be missing in this crowded field.</p>}
      <p className="field-hint">{data.source} · retrieved {new Date(data.retrieved_at).toLocaleDateString('en-GB', { timeZone: 'UTC' })}{data.cached ? ' · saved locally' : ''}</p>
      {data.cache_warning && <p className="object-error field-hint">{data.cache_warning}</p>}
    </>}
  </section>;
}
