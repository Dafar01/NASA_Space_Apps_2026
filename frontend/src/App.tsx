import { useCallback, useRef, useState } from 'react';
import { ArrowUpRight, Film, Telescope } from 'lucide-react';
import atlasLogo from '../../assets/ATLAS LOGO.png';
import { AmbienceAudio } from './components/AmbienceAudio';
import { SplashScreen } from './components/SplashScreen';
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
  const [showSplash, setShowSplash] = useState(true);
  const finishSplash = useCallback(() => setShowSplash(false), []);
  const viewer = useRef<SkyViewerHandle>(null);
  const queuedNavigation = useRef<{ position: SkyPosition; fov?: number } | null>(null);
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);
  const [tab, setTab] = useState<Tab>('sky');
  const [position, setPosition] = useState<SkyPosition | null>(null);
  const [showInfo, setShowInfo] = useState(false);
  const obs = useObservations();
  function changeTab(next: Tab) {
    setTab(next);
    if (next === 'observations' && position) obs.ensure(position);
  }
  function navigate(next: SkyPosition, fov?: number) {
    setPosition(next); setShowInfo(true); setTab('sky');
    if (viewer.current) viewer.current.navigate(next, fov);
    else queuedNavigation.current = { position: next, fov };
    if (!samePosition(next, position)) obs.cancel();
  }
  function select(next: SkyPosition) {
    if (!samePosition(next, position)) obs.cancel();
    setPosition(next); setShowInfo(true);
  }
  function openSaved(saved: SavedSeries) {
    setPosition(saved.query); setShowInfo(false);
    if (!samePosition(saved.query, position)) viewer.current?.navigate(saved.query, 0.5);
    obs.open(saved.query, saved.query.detector, saved);
  }
  return <>{showSplash && <SplashScreen onComplete={finishSplash} />}<div className="app" inert={showSplash} aria-hidden={showSplash || undefined}>
    <header className="topbar"><a href="./" className="brand" aria-label="Atlas - Sky Viewer home"><span className="brand-logo"><img src={atlasLogo} alt="Atlas" /></span><h1 className="sr-only">Atlas - Sky Viewer</h1></a>
      <nav className="view-tabs" role="tablist" aria-label="Explorer views">{(['sky', 'observations'] as const).map((value, index) => <button key={value} ref={element => { tabs.current[index] = element; }} id={`${value}-tab`} role="tab" aria-selected={tab === value} aria-controls={`${value}-panel`} tabIndex={tab === value ? 0 : -1} onClick={() => changeTab(value)} onKeyDown={event => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) { event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index; changeTab(next === 0 ? 'sky' : 'observations'); tabs.current[next]?.focus(); } }}>{value === 'sky' ? <Telescope size={17} /> : <Film size={17} />}{value === 'sky' ? 'Sky view' : 'Observation playback'}</button>)}</nav>
      <div className="header-actions"><AmbienceAudio /><a className="about-link" href={SPHEREX.documentation} target="_blank" rel="noreferrer"><span className="status-dot" />NASA SPHEREx<ArrowUpRight size={14} /></a></div>
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
  </div></>;
}
