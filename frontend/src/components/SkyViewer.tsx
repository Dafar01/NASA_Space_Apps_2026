import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { AlertCircle, LoaderCircle, MousePointer2, RefreshCw, WifiOff } from 'lucide-react';
import type { Aladin, Catalog } from 'aladin-lite';
import { checkTile, frequencyForFrame, INITIAL_VIEW, SPHEREX, SurveyError, validateSurvey } from '../config/surveys';
import { formatDegrees, normalizePosition } from '../types/astronomy';
import type { SkyPosition, ViewerStatus } from '../types/astronomy';
import { SkyControls } from './SkyControls';
import { ObjectPopover } from './ObjectPopover';
import type { MapAnchor } from './ObjectPopover';

export interface SkyViewerHandle {
  navigate: (position: SkyPosition, fov?: number) => void;
}

export interface SkyViewerProps {
  selectedPosition?: SkyPosition | null;
  onPositionSelect?: (position: SkyPosition) => void;
  onStatusChange?: (status: ViewerStatus) => void;
  onReadyChange?: (ready: boolean) => void;
  offline?: boolean;
  showInfo?: boolean;
  onCloseInfo?: () => void;
  onExplore?: () => void;
}

const INITIAL_STATUS: ViewerStatus = { phase: 'initializing', message: 'Initializing viewer' };

// This pinned npm build has no public viewer destructor (remove removes a
// layer). Stop its observer and animation loop in this one isolated adapter.
function disposeViewer(viewer: Aladin) {
  viewer.on('click', undefined);
  for (const event of ['positionChanged', 'zoomChanged', 'resizeChanged', 'stackChanged'] as const) viewer.on(event, undefined);
  viewer.view.resizeObserver.disconnect();
  viewer.view.redrawClbk = () => undefined;
}

function guardProjectionConversion(viewer: Aladin) {
  const convert = viewer.pix2world.bind(viewer);
  // This npm build destructures an undefined WASM result outside the Aitoff
  // ellipse. Its own click/mouse handlers expect undefined for those pixels.
  // Adapt this instance's public conversion API before registering callbacks.
  viewer.pix2world = (x, y, frame) => {
    try {
      const world = convert(x, y, frame);
      return world?.every(Number.isFinite) ? world : undefined;
    } catch { return undefined; }
  };
}

export const SkyViewer = forwardRef<SkyViewerHandle, SkyViewerProps>(function SkyViewer(
  { selectedPosition = null, onPositionSelect, onStatusChange, onReadyChange, offline = false, showInfo = false, onCloseInfo, onExplore }, ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef(selectedPosition);
  const pendingNavigation = useRef<{ position: SkyPosition; fov: number } | null>(null);
  const lastPointer = useRef<{ x: number; y: number } | null>(null);
  const pointerFrame = useRef<number | null>(null);
  const [pointer, setPointer] = useState<SkyPosition | null>(null);
  const [anchor, setAnchor] = useState<MapAnchor | null>(null);
  const viewerRef = useRef<Aladin | null>(null);
  const catalogRef = useRef<Catalog | null>(null);
  const sourceFactory = useRef<((ra: number, dec: number) => import('aladin-lite').Source) | null>(null);
  const callbacks = useRef({ onPositionSelect, onStatusChange });
  const healthCheck = useRef<(() => void) | null>(null);
  const [status, setStatus] = useState<ViewerStatus>(INITIAL_STATUS);
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [view, setView] = useState({ ...INITIAL_VIEW });
  function updatePointer() {
    const world = lastPointer.current && viewerRef.current?.pix2world(lastPointer.current.x, lastPointer.current.y, 'ICRS');
    setPointer(world ? normalizePosition(world[0], world[1]) : null);
  }
  function updateAnchor() {
    const position = selectedRef.current;
    const stage = stageRef.current;
    if (!position || !stage) { setAnchor(null); return; }
    const { width, height } = stage.getBoundingClientRect();
    let pixel: [number, number] | undefined;
    try { pixel = viewerRef.current?.world2pix(position.ra, position.dec, 'ICRS'); } catch { /* Outside the projection. */ }
    if (offline) pixel = [width / 2, height / 2];
    setAnchor(pixel && pixel.every(Number.isFinite) && pixel[0] >= 0 && pixel[0] <= width && pixel[1] >= 0 && pixel[1] <= height
      ? { x: pixel[0], y: pixel[1], width, height } : null);
  }
  useEffect(() => { selectedRef.current = selectedPosition; updateAnchor(); }, [selectedPosition, ready, offline]);
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(updateAnchor);
    observer.observe(stage);
    return () => { observer.disconnect(); if (pointerFrame.current !== null) cancelAnimationFrame(pointerFrame.current); };
  }, [offline]);
  useEffect(() => { callbacks.current = { onPositionSelect, onStatusChange }; }, [onPositionSelect, onStatusChange]);
  useEffect(() => { onReadyChange?.(ready); }, [ready, onReadyChange]);

  function mark(position: SkyPosition | null) {
    const catalog = catalogRef.current;
    catalog?.removeAll();
    if (position && sourceFactory.current) catalog?.addSources([sourceFactory.current(position.ra, position.dec)]);
  }

  useImperativeHandle(ref, () => ({
    navigate(position, fov = 10) {
      const value = normalizePosition(position.ra, position.dec);
      const viewer = viewerRef.current;
      if (!value) return;
      pendingNavigation.current = { position: value, fov };
      if (!viewer || !ready) return;
      viewer.gotoRaDec(value.ra, value.dec);
      viewer.setFoV(Math.max(0.1, Math.min(360, fov)));
      mark(value);
      pendingNavigation.current = null;
      updateAnchor();
    },
  }), [ready]);

  useEffect(() => { mark(selectedPosition); }, [selectedPosition, ready]);
  useEffect(() => {
    const pending = pendingNavigation.current;
    if (ready && pending && viewerRef.current) {
      viewerRef.current.gotoRaDec(pending.position.ra, pending.position.dec);
      viewerRef.current.setFoV(pending.fov);
      pendingNavigation.current = null;
      updateAnchor();
    }
  }, [ready]);

  useEffect(() => {
    if (offline) {
      setReady(false);
      const next: ViewerStatus = { phase: 'offline', message: 'The full-sky map needs a connection', detail: 'You can still select a preset, enter coordinates, and play saved observations.' };
      setStatus(next);
      callbacks.current.onStatusChange?.(next);
      return;
    }
    if (!containerRef.current) return;
    const container: HTMLDivElement = containerRef.current;
    const controller = new AbortController();
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(30000)]);
    let disposed = false;
    let viewer: Aladin | null = null;
    let tileController: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    function report(next: ViewerStatus) {
      if (disposed) return;
      setStatus(next);
      callbacks.current.onStatusChange?.(next);
    }
    function reportError(error: unknown) {
      if (disposed) return;
      const kind = error instanceof SurveyError ? error.kind : 'compatibility';
      report({ phase: 'error', message: kind === 'missing' ? 'Missing SPHEREx imagery' : kind === 'network' ? 'SPHEREx network failure' : 'Viewer compatibility error',
        detail: error instanceof Error ? error.message : String(error) });
    }
    async function inspectLatestTile() {
      tileController?.abort();
      const checkController = new AbortController();
      tileController = checkController;
      const tileSignal = AbortSignal.any([controller.signal, checkController.signal, AbortSignal.timeout(15000)]);
      const tiles = performance.getEntriesByType('resource').filter(entry => entry.name.startsWith(`${SPHEREX.url}/Norder`) && entry.name.endsWith(`_${SPHEREX.frame}.png`));
      const latest = tiles.at(-1)?.name ?? `${SPHEREX.url}/Norder0/Dir0/Npix0_${SPHEREX.frame}.png`;
      try {
        await checkTile(latest, tileSignal);
        report({ phase: 'ready', message: 'Viewer ready', detail: 'Sampled imagery tile is available. Faint regions can look dark; brightness alone does not indicate missing data.' });
      } catch (error) {
        if (controller.signal.aborted || checkController.signal.aborted) return;
        reportError(tileSignal.aborted ? new SurveyError('network', 'The SPHEREx tile request timed out. Try checking imagery again.') : error);
      }
    }
    function scheduleTileCheck() {
      clearTimeout(timer);
      tileController?.abort();
      timer = setTimeout(() => { void inspectLatestTile(); }, 1000);
    }
    function updateView() {
      if (disposed || !viewer) return;
      const center = viewer.getRaDec();
      const position = normalizePosition(center[0], center[1]);
      if (position) setView({ ...position, fov: viewer.getFov()[0] });
      updateAnchor();
      updatePointer();
      scheduleTileCheck();
    }
    async function initialize() {
      setReady(false);
      report(INITIAL_STATUS);
      try {
        const { default: A } = await import('aladin-lite');
        await A.init;
        if (disposed) return;
        report({ phase: 'loading', message: 'Loading sky survey', detail: 'Checking NASA metadata and a real SPHEREx image tile…' });
        await validateSurvey(signal);
        if (disposed) return;
        const survey = A.HiPS(SPHEREX.url, {
          name: SPHEREX.name,
          imgFormat: 'png',
          successCallback(layer) {
            if (disposed) return;
            try {
              // Run before the first rendering tick: the library default is
              // frame 50, one of NASA's two intentionally blank channels.
              layer.setFrequency({ value: frequencyForFrame(SPHEREX.frame, layer.cubeDepth, layer.emMin, layer.emMax), unit: 'Hz' });
              viewer?.setFoV(INITIAL_VIEW.fov);
              setReady(true);
              scheduleTileCheck();
            } catch (error) { reportError(error); }
          },
          errorCallback: reportError,
        });
        viewer = A.aladin(container, {
          survey,
          target: `${INITIAL_VIEW.ra} ${INITIAL_VIEW.dec}`,
          fov: INITIAL_VIEW.fov,
          projection: 'AIT', cooFrame: 'ICRS', mode: 'dark',
          showZoomControl: false, showLayersControl: false, showFullscreenControl: false,
          showFrame: false, showFov: false, showCooLocation: false, showProjectionControl: false,
          showStatusBar: false, showReticle: false, showCooGrid: false,
          backgroundColor: '#080d14', log: false, inertia: false,
        });
        viewerRef.current = viewer;
        guardProjectionConversion(viewer);
        viewer.setFoVRange(0.1, 360);
        const catalog = A.catalog({ name: 'Selected ICRS position', color: '#e5ad82', sourceSize: 22, shape: 'cross' });
        viewer.addCatalog(catalog);
        catalogRef.current = catalog;
        sourceFactory.current = A.source;
        viewer.on('click', event => {
          if (disposed || event.isDragging || !viewer) return;
          let ra = event.ra;
          let dec = event.dec;
          // The pinned v3 click event supplies ICRS coordinates directly.
          // If a later compatible build supplies only pixels, convert that click.
          if (ra === undefined || dec === undefined) {
            try { const world = viewer.pix2world(event.x, event.y, 'ICRS'); ra = world?.[0]; dec = world?.[1]; }
            catch { return; } // Outside the valid projection.
          }
          if (ra === undefined || dec === undefined) return;
          const value = normalizePosition(ra, dec);
          if (!value) return;
          mark(value);
          callbacks.current.onPositionSelect?.(value);
        });
        viewer.on('positionChanged', updateView);
        viewer.on('zoomChanged', updateView);
        viewer.on('resizeChanged', updateView);
        // Never expose the library's automatic DSS fallback as SPHEREx.
        viewer.on('stackChanged', () => {
          if (disposed || !viewer) return;
          const layer = viewer.getBaseImageLayer();
          if (layer && layer.url !== SPHEREX.url) {
            setReady(false);
            reportError(new SurveyError('compatibility', 'Aladin changed the survey unexpectedly. Reload to restore SPHEREx.'));
          }
        });
        healthCheck.current = () => { void inspectLatestTile(); };
        updateView();
      } catch (error) {
        if (disposed) return;
        reportError(signal.aborted ? new SurveyError('network', 'NASA data loading timed out. Check your connection and retry.') : error);
      }
    }
    void initialize();
    return () => {
      disposed = true;
      controller.abort();
      tileController?.abort();
      clearTimeout(timer);
      healthCheck.current = null;
      if (viewer) disposeViewer(viewer);
      viewerRef.current = null;
      catalogRef.current = null;
      sourceFactory.current = null;
      container.replaceChildren();
    };
  }, [attempt, offline]);

  function reset() {
    const viewer = viewerRef.current;
    if (!viewer) return;
    viewer.setProjection('AIT');
    viewer.setRotation(0);
    viewer.gotoRaDec(INITIAL_VIEW.ra, INITIAL_VIEW.dec);
    viewer.setFoV(INITIAL_VIEW.fov);
  }
  function retry() {
    if (ready && healthCheck.current) healthCheck.current();
    else setAttempt(value => value + 1);
  }
  return <section className="viewer-shell" aria-label="Interactive SPHEREx sky viewer">
    <div ref={stageRef} data-status={status.phase} className={`map-stage ${offline ? 'offline-map' : ''}`} onPointerMoveCapture={event => {
      if (!containerRef.current?.contains(event.target as Node)) return;
      const rect = containerRef.current.getBoundingClientRect();
      lastPointer.current = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      if (pointerFrame.current === null) pointerFrame.current = requestAnimationFrame(() => { pointerFrame.current = null; updatePointer(); });
    }} onPointerLeave={() => { lastPointer.current = null; setPointer(null); }}>
      <div ref={containerRef} className={`aladin-host ${!ready && status.phase === 'error' ? 'viewer-hidden' : ''}`} data-testid="sky-map" />
      {!offline && <div className="map-label"><span className="status-dot" />SPHEREx DR1<span className="map-label-divider" />{SPHEREX.wavelength} μm</div>}
      {!offline && <SkyControls disabled={!ready} onReset={reset} onZoom={factor => {
        const viewer = viewerRef.current;
        if (viewer) viewer.setFoV(Math.max(0.1, Math.min(360, viewer.getFov()[0] * factor)));
      }} />}
      {!ready && <div className="viewer-overlay"><div className="load-card">
        {offline ? <WifiOff size={26} /> : status.phase === 'error' ? <AlertCircle size={26} /> : <LoaderCircle size={26} className="spin" />}
        <h3>{status.message}</h3><p>{status.detail ?? 'Starting the astronomical rendering engine…'}</p>
        {status.phase === 'error' && <button className="button button-secondary" onClick={retry}><RefreshCw size={14} />Retry SPHEREx</button>}
      </div></div>}
      {!offline && <div className="map-hint"><MousePointer2 size={13} /><span>Drag to explore<span className="hint-separator">·</span>Scroll to zoom<span className="hint-separator">·</span>Click to select</span></div>}
      {showInfo && selectedPosition && <ObjectPopover position={selectedPosition} anchor={anchor} offline={offline} onClose={() => onCloseInfo?.()} onExplore={() => onExplore?.()} />}
    </div>
    {!offline && <div className="viewer-readout"><div><span className="readout-label">POINTER</span><span className="coordinate-value" data-testid="pointer-coordinates">{pointer ? formatDegrees(pointer.ra) : '—'}<span className="readout-slash">/</span>{pointer ? formatDegrees(pointer.dec, true) : '—'}</span></div><div className="fov-readout"><span className="readout-label">FIELD OF VIEW</span><span className="coordinate-value" data-testid="field-of-view">{view.fov.toFixed(2)}°</span></div><span className="readout-frame">ICRS</span></div>}
    {status.phase === 'error' && ready && <div className="error-banner" role="alert"><AlertCircle size={15} /><span>{status.detail}</span><button className="text-button" onClick={retry}>Retry imagery</button></div>}
  </section>;
});
