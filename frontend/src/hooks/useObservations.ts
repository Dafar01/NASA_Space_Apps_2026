import { useCallback, useEffect, useRef, useState } from 'react';
import type { SkyPosition } from '../types/astronomy';
import type { ObservationSeries, PendingSeries, SavedSeries, SeriesQuery, SeriesResponse } from '../types/observations';
import { apiRequest } from '../types/observations';
import { DEFAULT_PATCH_SIZE, groupSavedObjects, samePosition } from '../config/bands';

interface Selection { key: number; query: SeriesQuery; savedId?: string }
interface Resource { key: number; series: ObservationSeries | null; progress: PendingSeries | null; error: string }

export function useObservations() {
  const [library, setLibrary] = useState<SavedSeries[]>([]);
  const [offline, setOffline] = useState(false);
  const [checked, setChecked] = useState(false);
  const [ready, setReady] = useState(false);
  const [libraryError, setLibraryError] = useState('');
  const [selection, setSelection] = useState<Selection | null>(null);
  const [resource, setResource] = useState<Resource | null>(null);
  const serial = useRef(0);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const health = await apiRequest<{ offline: boolean }>('/api/health', { signal });
      if (signal?.aborted) return;
      setOffline(health.offline);
      const response = await apiRequest<{ series: SavedSeries[] }>('/api/series', { signal });
      if (signal?.aborted) return;
      setLibrary(response.series);
      setReady(true);
      setLibraryError('');
    } catch {
      if (!signal?.aborted) setLibraryError('Observation service unavailable. Start the Python backend, then retry.');
    } finally {
      if (!signal?.aborted) setChecked(true);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    if (!selection || !ready) return;
    const controller = new AbortController();
    const signal = controller.signal;
    const deadline = Date.now() + 8 * 60 * 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const base: Resource = { key: selection.key, series: null, progress: null, error: '' };
    setResource({ ...base, progress: { id: '', state: 'preparing', query: selection.query,
      message: selection.savedId ? 'Opening saved observations…' : 'Finding observations…', completed: 0, total: 0 } });

    function fail(error: unknown) {
      if (!signal.aborted) setResource({ ...base, error: error instanceof Error ? error.message : 'Unable to load observations.' });
    }
    async function receive(response: SeriesResponse) {
      if (signal.aborted) return;
      if (Date.now() > deadline) throw new Error('IRSA preparation is taking longer than expected. It may continue saving in the backend. Retry to reconnect, or open a saved object.');
      if (response.state === 'error') throw new Error(response.message);
      if (response.state === 'preparing') {
        setResource({ ...base, progress: response });
        timer = setTimeout(() => {
          void apiRequest<SeriesResponse>(`/api/series/${response.id}`, { signal }).then(receive).catch(fail);
        }, 1000);
        return;
      }
      if (!('frames' in response)) throw new Error('The observation service returned an invalid sequence.');
      setResource({ ...base, series: response });
      void apiRequest<{ series: SavedSeries[] }>('/api/series', { signal }).then(result => {
        if (!signal.aborted) setLibrary(result.series);
      }).catch(() => { /* The loaded sequence remains usable. */ });
    }
    const request = selection.savedId
      ? apiRequest<SeriesResponse>(`/api/series/${selection.savedId}`, { signal })
      : apiRequest<SeriesResponse>(`/api/series?cache_only=${offline}`, { method: 'POST', signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(selection.query) });
    void request.then(receive).catch(fail);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [selection, ready, offline]);

  const open = useCallback((position: SkyPosition, detector?: string, saved?: SavedSeries) => {
    const object = groupSavedObjects(library).find(object => samePosition(object.position, position));
    const chosen = saved ?? object?.bands.find(band => band.query.detector === (detector ?? 'D2'))
      ?? (!detector ? object?.bands[0] : undefined);
    const query: SeriesQuery = chosen?.query ?? { ra: position.ra, dec: position.dec, detector: detector ?? 'D2', size_deg: DEFAULT_PATCH_SIZE };
    setSelection({ key: ++serial.current, query, savedId: chosen?.id });
  }, [library]);

  function ensure(position: SkyPosition) {
    if (!selection || !samePosition(selection.query, position)) open(position);
  }
  function cancel() { setSelection(null); setResource(null); }
  function retry() {
    if (selection) setSelection({ ...selection, key: ++serial.current });
    if (!ready) void refresh();
  }
  const current = selection && resource?.key === selection.key ? resource : null;
  const pending: PendingSeries | null = selection && !current ? { id: '', state: 'preparing', query: selection.query,
    message: ready ? 'Opening observations…' : 'Connecting to observation service…', completed: 0, total: 0 } : null;
  const serviceError = selection && checked && !ready ? libraryError : '';
  return { library, offline, checked, libraryError, selection, series: current?.series ?? null,
    progress: serviceError ? null : current?.progress ?? pending, error: current?.error || serviceError, open, ensure, cancel, retry, refresh };
}

export type Observations = ReturnType<typeof useObservations>;
