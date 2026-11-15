import { useEffect, useState } from 'react';
import atlasLogo from '../../../assets/ATLAS LOGO.png';

export function SplashScreen({ onComplete }: { onComplete: () => void }) {
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    let fade: ReturnType<typeof setTimeout> | undefined;
    const hold = setTimeout(() => {
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) onComplete();
      else {
        setLeaving(true);
        fade = setTimeout(onComplete, 600);
      }
    }, 3000);
    return () => { clearTimeout(hold); clearTimeout(fade); };
  }, [onComplete]);

  return <div className={`splash-screen${leaving ? ' is-leaving' : ''}`} role="status" aria-label="Loading Atlas">
    <span className="brand-logo splash-logo"><img src={atlasLogo} alt="Atlas — Sky Viewer" fetchPriority="high" /></span>
  </div>;
}
