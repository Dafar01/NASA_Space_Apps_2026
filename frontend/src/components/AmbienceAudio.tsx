import { useEffect, useRef, useState } from 'react';
import { Volume2, VolumeX } from 'lucide-react';
import ambience from '../../../assets/Space_Ambience_Audio.mp3';

export function AmbienceAudio() {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [enabled, setEnabled] = useState(() => {
    try { return localStorage.getItem('atlas-ambience') !== 'off'; }
    catch { return true; }
  });

  useEffect(() => {
    const element = audio.current!;
    element.volume = 0.18;
    if (!enabled || unavailable) { element.pause(); return; }
    let disposed = false;
    let pending = false;
    async function start() {
      if (pending || !element.paused) return;
      pending = true;
      try {
        await element.play();
        if (disposed) element.pause();
        else removeListeners();
      } catch { /* Autoplay may be blocked; retry on the next user gesture. */ }
      finally { pending = false; }
    }
    function onKey(event: KeyboardEvent) {
      if (!['Shift', 'Control', 'Alt', 'Meta', 'Escape'].includes(event.key)) void start();
    }
    function removeListeners() {
      window.removeEventListener('pointerdown', start);
      window.removeEventListener('keydown', onKey);
    }
    window.addEventListener('pointerdown', start);
    window.addEventListener('keydown', onKey);
    void start();
    return () => { disposed = true; removeListeners(); element.pause(); };
  }, [enabled, unavailable]);

  function toggle() {
    const next = !enabled || !playing;
    setEnabled(next);
    try { localStorage.setItem('atlas-ambience', next ? 'on' : 'off'); } catch { /* Storage is optional. */ }
    if (!next) audio.current?.pause();
    else if (audio.current) void audio.current.play().catch(() => { /* Remain available for another gesture. */ });
  }
  return <>
    <audio ref={audio} src={ambience} loop preload="none" aria-hidden="true" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onError={() => { setUnavailable(true); setPlaying(false); }} />
    <button className="icon-button ambience-toggle" type="button" aria-label={unavailable ? 'Ambience audio unavailable' : playing ? 'Mute ambience' : 'Play ambience'} title={unavailable ? 'Ambience audio could not be loaded' : playing ? 'Mute ambience' : 'Play ambience'} aria-pressed={playing} disabled={unavailable} onClick={toggle}>{playing ? <Volume2 size={18} /> : <VolumeX size={18} />}</button>
  </>;
}
