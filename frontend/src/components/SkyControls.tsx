import { Minus, Plus, RotateCcw } from 'lucide-react';

interface SkyControlsProps {
  disabled: boolean;
  onZoom: (factor: number) => void;
  onReset: () => void;
}

export function SkyControls({ disabled, onZoom, onReset }: SkyControlsProps) {
  return <div className="map-controls" role="group" aria-label="Sky viewer controls">
    <button disabled={disabled} onClick={() => onZoom(0.65)} title="Zoom in" aria-label="Zoom in"><Plus size={19} /></button>
    <button disabled={disabled} onClick={() => onZoom(1 / 0.65)} title="Zoom out" aria-label="Zoom out"><Minus size={19} /></button>
    <span className="control-divider" />
    <button disabled={disabled} onClick={onReset} title="Reset sky view" aria-label="Reset sky view"><RotateCcw size={17} /></button>
  </div>;
}
