import { useRef, useState } from 'react';
import { ArrowUpRight, Film, Orbit, Telescope } from 'lucide-react';
import { SkyViewer } from './components/SkyViewer';
import type { SkyViewerHandle } from './components/SkyViewer';
import { LocationPanel } from './components/LocationPanel';
import { ObservationTimeline } from './components/ObservationTimeline';
import { useObservations } from './hooks/useObservations';
import { samePosition } from './config/bands';
import type { SkyPosition } from './types/astronomy';
import type { SavedSeries } from './types/observations';
import { SPHEREX } from './config/surveys';

type Tab = 'sky' | 'observations';
export default function App() {
  const viewer = useRef<SkyViewerHandle>(null);
  const queuedNavigation = useRef<{ position: SkyPosition; fov?: number } | null>(null);
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);
  const [tab, setTab] = useState<Tab>('sky');
  const [position, setPosition] = useState<SkyPosition | null>(null);
  const [showInfo, setShowInfo] = useState(false);
  const obs = useObservations();
  function reveal(next: Tab) {
    requestAnimationFrame(() => document.getElementById(`${next}-panel`)?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start',
    }));
  }
  function changeTab(next: Tab) {
    setTab(next);
    if (next === 'observations' && position) obs.ensure(position);
    reveal(next);
  }
  function navigate(next: SkyPosition, fov?: number) {
    setPosition(next); setShowInfo(true); setTab('sky');
    if (viewer.current) viewer.current.navigate(next, fov);
    else queuedNavigation.current = { position: next, fov };
    if (!samePosition(next, position)) obs.cancel();
    reveal('sky');
  }
  function select(next: SkyPosition) {
    if (!samePosition(next, position)) obs.cancel();
    setPosition(next); setShowInfo(true);
  }
  function openSaved(saved: SavedSeries) {
    setPosition(saved.query); setShowInfo(false);
    viewer.current?.navigate(saved.query, 0.5);
    obs.open(saved.query, saved.query.detector, saved);
    reveal('observations');
  }
  return <div className="app">
    <header className="topbar"><a href="./" className="brand" aria-label="Atlas - Sky Viewer home"><span className="brand-symbol"><Orbit size={30} strokeWidth={1.3} /></span><span><h1>Atlas<span>Sky Viewer</span></h1><p>DISCOVER THE CHANGING SKY</p></span></a>
      <nav className="view-tabs" role="tablist" aria-label="Explorer views">{(['sky', 'observations'] as const).map((value, index) => <button key={value} ref={element => { tabs.current[index] = element; }} id={`${value}-tab`} role="tab" aria-selected={tab === value} aria-controls={`${value}-panel`} tabIndex={tab === value ? 0 : -1} onClick={() => changeTab(value)} onKeyDown={event => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) { event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index; changeTab(next === 0 ? 'sky' : 'observations'); tabs.current[next]?.focus(); } }}>{value === 'sky' ? <Telescope size={17} /> : <Film size={17} />}{value === 'sky' ? 'Sky view' : 'Observation playback'}</button>)}</nav>
      <a className="about-link" href={SPHEREX.documentation} target="_blank" rel="noreferrer"><span className="status-dot" />NASA SPHEREx<ArrowUpRight size={14} /></a>
    </header>
    <main className="workspace"><div className="tab-panels">
      <section className="tab-panel sky-layout" id="sky-panel" role="tabpanel" aria-labelledby="sky-tab" hidden={tab !== 'sky'}>
        <div className="sky-column">{obs.checked ? <SkyViewer ref={viewer} selectedPosition={position} onPositionSelect={select} offline={obs.offline} showInfo={showInfo} onCloseInfo={() => setShowInfo(false)} onExplore={() => changeTab('observations')} onReadyChange={ready => {
          const queued = queuedNavigation.current;
          if (ready && queued) { viewer.current?.navigate(queued.position, queued.fov); queuedNavigation.current = null; }
        }} /> : <div className="map-stage initial-stage"><span className="eyebrow">OPENING THE SKY…</span></div>}
        </div>
        <LocationPanel position={position} onNavigate={navigate} />
      </section>
      <section className="tab-panel" id="observations-panel" role="tabpanel" aria-labelledby="observations-tab" hidden={tab !== 'observations'}><ObservationTimeline observations={obs} active={tab === 'observations'} onOpenSaved={openSaved} onSky={() => changeTab('sky')} /></section>
    </div></main>
  </div>;
}
