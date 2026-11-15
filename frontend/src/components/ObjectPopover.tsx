import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowUpRight, Check, Copy, X } from 'lucide-react';
import type { SkyPosition } from '../types/astronomy';
import { formatDegrees } from '../types/astronomy';
import { ObjectInfo } from './ObjectInfo';
import { SkyObservationInfo } from './SkyObservationInfo';

export interface MapAnchor { x: number; y: number; width: number; height: number }
interface Props { position: SkyPosition; anchor: MapAnchor | null; offline: boolean; onClose: () => void; onExplore: () => void }
export function ObjectPopover({ position, anchor: projected, offline, onClose, onExplore }: Props) {
  const anchor = projected ?? { x: 180, y: 240, width: 360, height: 480 };
  const card = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(400);
  const [copied, setCopied] = useState('');
  useEffect(() => { setCopied(''); }, [position]);
  useLayoutEffect(() => {
    const element = card.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setHeight(element.getBoundingClientRect().height));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    function keydown(event: KeyboardEvent) { if (event.key === 'Escape') onClose(); }
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, [onClose]);
  const width = Math.min(338, anchor.width - 24);
  const left = Math.max(12, Math.min(anchor.width - width - 12, anchor.x + 24 + width < anchor.width ? anchor.x + 24 : anchor.x - width - 24));
  const top = Math.max(12, Math.min(anchor.height - height - 12, anchor.y - height / 2));
  const tipX = Math.max(left + 12, Math.min(left + width - 12, anchor.x));
  const tipY = Math.max(top + 12, Math.min(top + height - 12, anchor.y));
  async function copy() {
    try { await navigator.clipboard.writeText(`${position.ra.toFixed(6)}, ${position.dec.toFixed(6)}`); setCopied('Coordinates copied'); }
    catch { setCopied('Copy unavailable. Select and copy the coordinates.'); }
  }
  return <div className="object-popover-layer" hidden={!projected}>
    <svg className="popup-tether" width={anchor.width} height={anchor.height} aria-hidden="true"><path d={`M ${anchor.x} ${anchor.y} L ${tipX} ${tipY}`} /><circle cx={anchor.x} cy={anchor.y} r="5" /></svg>
    <div ref={card} className="object-popover" role="dialog" aria-label="Selected sky object" style={{ left, top, width, maxHeight: anchor.height - 24 }}>
      <div className="popover-heading"><span className="eyebrow">SELECTED SKY POSITION</span><button className="icon-button" aria-label="Close object information" onClick={onClose}><X size={17} /></button></div>
      <div className="popover-scroll"><div className="popup-coordinates" data-testid="selected-coordinates"><div><span>RA</span><strong data-testid="selected-ra">{formatDegrees(position.ra)}</strong></div><div><span>DEC</span><strong data-testid="selected-dec">{formatDegrees(position.dec, true)}</strong></div><button className="icon-button" title="Copy coordinates" aria-label="Copy coordinates" onClick={() => void copy()}>{copied === 'Coordinates copied' ? <Check size={15} /> : <Copy size={15} />}</button></div>{copied && <p className="field-hint" role="status">{copied}</p>}
        <ObjectInfo position={position} offline={offline} />
        <SkyObservationInfo position={position} offline={offline} />
      </div><div className="popover-footer"><button className="button button-primary full-width" onClick={onExplore}>View observations<ArrowUpRight size={15} /></button></div>
    </div>
  </div>;
}
