export interface FrameMetadata {
  wavelength_um: number | null; bandwidth_um: number | null; release: string;
  valid_fraction: number | null; observation_id: string;
  available_fraction?: number | null;
}
export function ObservationDetails({ frame }: { frame: FrameMetadata }) {
  return <dl className="object-properties observation-properties">
    <div><dt>Wavelength</dt><dd>{frame.wavelength_um === null ? 'Image analysis required' : `${frame.wavelength_um.toFixed(3)} µm`}</dd></div>
    <div><dt>Bandwidth</dt><dd>{frame.bandwidth_um === null ? 'Image analysis required' : `${frame.bandwidth_um.toFixed(3)} µm`}</dd></div>
    <div><dt>Release</dt><dd>{frame.release}</dd></div>
    {frame.available_fraction != null && <div><dt>Available pixels</dt><dd>{(frame.available_fraction * 100).toFixed(1)}%</dd></div>}
    <div><dt>Quality-checked pixels</dt><dd>{frame.valid_fraction === null ? 'Image analysis required' : `${(frame.valid_fraction * 100).toFixed(1)}%`}</dd></div>
    <div><dt>Observation ID</dt><dd title={frame.observation_id}>{frame.observation_id}</dd></div>
  </dl>;
}
