import { useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRight, Crosshair } from 'lucide-react';
import { parsePosition } from '../types/astronomy';
import type { SkyPosition } from '../types/astronomy';
import { TargetPresets } from './TargetPresets';

interface Props { position: SkyPosition | null; onNavigate: (position: SkyPosition, fov?: number) => void }
export function LocationPanel({ position, onNavigate }: Props) {
  const [ra, setRa] = useState('');
  const [dec, setDec] = useState('');
  const [error, setError] = useState('');
  function submit(event: FormEvent) {
    event.preventDefault();
    const value = parsePosition(ra, dec);
    if (!value) { setError('Enter decimal degrees. Declination must be between −90 and +90.'); return; }
    setError(''); onNavigate(value, 2);
  }
  return <aside className="location-panel" aria-label="Location explorer">
    <div className="panel-intro"><span className="eyebrow">FIND YOUR PERSPECTIVE</span><h2>A universe to explore.</h2><p>Find a familiar star or follow your curiosity into the infrared sky.</p></div>
    <section className="coordinate-section"><div className="section-heading"><h3>Go to coordinates</h3><Crosshair size={15} /></div><form onSubmit={submit} noValidate><div className="coordinate-inputs"><label>Right ascension<span className="input-wrap"><input aria-label="Right ascension in degrees" inputMode="decimal" placeholder="0.000000" value={ra} onChange={event => setRa(event.target.value)} /><span>°</span></span></label><label>Declination<span className="input-wrap"><input aria-label="Declination in degrees" inputMode="decimal" placeholder="+0.000000" value={dec} onChange={event => setDec(event.target.value)} /><span>°</span></span></label></div><p className="field-hint">ICRS · decimal degrees</p>{error && <p className="field-error" role="alert">{error}</p>}<button type="submit" className="button button-secondary full-width">Go to location<ArrowRight size={15} /></button></form></section>
    <TargetPresets disabled={false} position={position} onNavigate={onNavigate} />
  </aside>;
}
