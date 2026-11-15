import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, ArrowDownToLine, ArrowUpRight, ChevronDown, ChevronLeft, ChevronRight, Film, LoaderCircle, Pause, Play, RefreshCw, Waves } from 'lucide-react';
import type { Observations } from '../hooks/useObservations';
import { groupSavedObjects, samePosition, wavelengthLabel } from '../config/bands';
import { targetName } from '../config/targets';
import { observationDate } from '../types/observations';
import type { SavedSeries } from '../types/observations';
import { ObservationDetails } from './ObservationDetails';

interface Props { observations: Observations; active: boolean; onOpenSaved: (saved: SavedSeries) => void; onSky: () => void }

export function ObservationTimeline({ observations: obs, active, onOpenSaved, onSky }: Props) {
  const { selection, series, progress, error } = obs;
  const groups = useMemo(() => groupSavedObjects(obs.library), [obs.library]);
  const selectedGroup = groups.find(group => selection && samePosition(group.position, selection.query));
  const selectedKey = selectedGroup?.key;
  const [expanded, setExpanded] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState('2');
  const [imagesReady, setImagesReady] = useState(false);
  const [imageError, setImageError] = useState('');
  const [imageAttempt, setImageAttempt] = useState(0);
  const count = series?.frames.length ?? 0;
  const safeIndex = Math.min(index, Math.max(0, count - 1));
  const frame = series?.frames[safeIndex];

  useEffect(() => { if (selectedKey) setExpanded(selectedKey); }, [selectedKey]);
  useEffect(() => { if (!active) setPlaying(false); }, [active]);
  useEffect(() => {
    setIndex(0); setPlaying(false); setImagesReady(false); setImageError('');
    if (!series?.frames.length) return;
    let disposed = false;
    const images = series.frames.map(frame => {
      const image = new Image();
      return { image, promise: new Promise<void>((resolve, reject) => {
        image.onload = () => resolve();
        image.onerror = () => reject(new Error('An observation image could not be loaded. Retry the images.'));
        image.src = frame.image_url;
      }) };
    });
    void Promise.all(images.map(item => item.promise)).then(() => { if (!disposed) setImagesReady(true); })
      .catch(error => { if (!disposed) setImageError(error.message); });
    return () => { disposed = true; images.forEach(({ image }) => { image.onload = null; image.onerror = null; }); };
  }, [series, imageAttempt]);
  useEffect(() => {
    if (!active || !playing || !imagesReady || count < 2) return;
    const timer = setInterval(() => setIndex(value => (value + 1) % count), 1000 / Number(speed));
    return () => clearInterval(timer);
  }, [active, playing, imagesReady, count, speed]);

  function step(delta: number) { setPlaying(false); setIndex(value => (value + delta + count) % count); }
  return <div className="playback-layout">
    <section className="timeline-panel" aria-label="Chronological observations">
      <div className="playback-heading"><div><span className="eyebrow">OBSERVATION PLAYBACK</span><h2>{selection ? targetName(selection.query.ra, selection.query.dec) : 'Watch the sky change.'}</h2></div></div>
      {(error || imageError) && <div className="error-banner" role="alert"><AlertCircle size={17} /><span>{error || imageError}</span><button className="text-button" onClick={() => error ? obs.retry() : setImageAttempt(value => value + 1)}>Retry</button></div>}
      {frame && <section className="observation-details" aria-label="Observation details"><h3>Observation details</h3><ObservationDetails frame={frame} /><details className="provenance-details"><summary>Source and processing notes<ChevronDown size={14} /></summary>{series?.notes.map(note => <p key={note} className="field-hint">{note}</p>)}<a href={`/api/series/${series!.id}/manifest`} target="_blank" rel="noreferrer" className="object-source"><ArrowDownToLine size={13} />View sequence metadata</a></details></section>}
      <div className="observation-stage">
        {frame ? <><img key={frame.image_url} src={frame.image_url} alt={`SPHEREx observation of ${targetName(series!.query.ra, series!.query.dec)} on ${observationDate(frame.timestamp)}`} className="observation-image" /><span className="image-compass">N ↑<br />E ←</span><div className="frame-caption"><div><span className="eyebrow">OBSERVED ON</span><strong data-testid="observation-date">{observationDate(frame.timestamp, true)} UTC</strong></div><span className="frame-badge">{safeIndex + 1}<span> / {count}</span></span></div></> : <div className="player-empty">
          {progress ? <><LoaderCircle size={30} className="spin" /><h3>Preparing your view</h3><p role="status">{progress.message}</p>{progress.total > 0 && <progress value={progress.completed} max={progress.total} />}</> : <><Film size={36} strokeWidth={1} /><h3>{series?.state === 'empty' ? 'No observations here yet' : 'Every frame tells a story'}</h3><p>{series?.message || 'Choose an object from your collection, or explore a location in the sky view.'}</p><button className="button button-primary" onClick={onSky}>Explore the sky<ArrowUpRight size={15} /></button></>}
        </div>}
      </div>
      <div className="timeline-player">
        <div className="transport"><button className="icon-button" aria-label="Previous observation" disabled={!imagesReady || count < 2} onClick={() => step(-1)}><ChevronLeft size={21} /></button><button className="play-button" aria-label={playing ? 'Pause playback' : 'Play observations'} disabled={!imagesReady || count < 2} onClick={() => setPlaying(value => !value)}>{playing ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" />}</button><button className="icon-button" aria-label="Next observation" disabled={!imagesReady || count < 2} onClick={() => step(1)}><ChevronRight size={21} /></button></div>
        <div className="scrubber"><input type="range" aria-label="Observation timeline" min={0} max={Math.max(0, count - 1)} value={safeIndex} disabled={!imagesReady || count < 2} onChange={event => { setPlaying(false); setIndex(Number(event.target.value)); }} /><div className="timeline-endpoints"><span>{series?.date_start ? observationDate(series.date_start) : 'Earlier'}</span><span>{series?.date_end ? observationDate(series.date_end) : 'Later'}</span></div></div>
        <label className="speed-control"><span className="sr-only">Playback speed</span><select aria-label="Playback speed" value={speed} onChange={event => setSpeed(event.target.value)}><option value="1">1×</option><option value="2">2×</option><option value="4">4×</option></select></label>
      </div>
      <div className="playback-note"><span>{count ? `${count} observations${imagesReady ? '' : ' · loading previews'}` : 'Chronological sequence'}</span><span>Time gaps vary · no interpolated frames</span></div>
    </section>
    <aside className="saved-library" aria-label="Saved observations"><div className="collection-heading"><div><span className="eyebrow">YOUR COLLECTION</span><h3>Saved objects <span>{groups.length}</span></h3></div><button className="icon-button" aria-label="Refresh saved observations" onClick={() => void obs.refresh()}><RefreshCw size={16} /></button></div><p className="field-hint">Choose an object, then explore its infrared wavelengths.</p>
      {obs.libraryError && <p className="field-error" role="alert">{obs.libraryError}</p>}
      <div className="saved-object-list">{groups.map(group => <div key={group.key} className={`saved-object ${selectedKey === group.key ? 'is-selected' : ''}`}><button className="saved-object-toggle" aria-expanded={expanded === group.key} onClick={() => { if (expanded === group.key) setExpanded(null); else { setExpanded(group.key); onOpenSaved(group.bands.find(saved => saved.query.detector === 'D2') ?? group.bands[0]); } }}><span><strong>{group.name}</strong><small>{group.bands.length} wavelength {group.bands.length === 1 ? 'band' : 'bands'}</small></span><ChevronDown size={17} /></button><div className="saved-band-list" hidden={expanded !== group.key} role="group" aria-label={`${group.name} wavelengths`}>{group.bands.map(saved => <button key={saved.id} className={`saved-band ${selection?.savedId === saved.id || series?.id === saved.id ? 'is-active' : ''}`} onClick={() => onOpenSaved(saved)}><span className="band-dot" /><span><strong>{wavelengthLabel(saved)}</strong><small>{saved.frame_count} frames{saved.date_start ? ` · ${observationDate(saved.date_start)}` : ''}</small></span><span className="band-id">{saved.query.detector}</span></button>)}</div></div>)}</div>
      {!groups.length && <div className="collection-empty"><Waves size={24} /><p>Your collection starts with a location.</p><span>Choose a sky position, then open playback to load its observations.</span></div>}
      <p className="collection-footnote">Downloaded observations stay on this computer for the next visit.</p>
    </aside>
  </div>;
}
