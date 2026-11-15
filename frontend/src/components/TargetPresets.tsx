import { ArrowUpRight } from 'lucide-react';
import type { SkyPosition } from '../types/astronomy';
import { TARGETS } from '../config/targets';
import { samePosition } from '../config/bands';

export function TargetPresets({ disabled, position = null, onNavigate }: { disabled: boolean; position?: SkyPosition | null; onNavigate: (position: SkyPosition, fov?: number) => void }) {
  return <section className="panel-section">
    <div className="section-heading"><h3>Start exploring</h3><span>{String(TARGETS.length).padStart(2, '0')} TARGETS</span></div>
    <div className="target-list">{TARGETS.map((target, index) =>
      <button key={target.name} disabled={disabled} className={`target ${samePosition(target, position) ? 'is-active' : ''}`} onClick={() => onNavigate(target, target.fov)}>
        <span className="target-number">0{index + 1}</span>
        <span><strong>{target.name}</strong><small>{target.description}</small></span>
        <ArrowUpRight size={16} />
      </button>)}</div>
  </section>;
}
