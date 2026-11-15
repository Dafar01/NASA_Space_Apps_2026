// Narrow declarations for the APIs used from pinned aladin-lite 3.9.2-beta.
// The npm distribution ships JavaScript only. Checked against its bundle and
// https://cds-astro.github.io/aladin-lite/Aladin.html (see README limitations).
declare module 'aladin-lite' {
  export interface HiPS {
    url: string;
    added: boolean;
    cubeDepth: number;
    emMin: number;
    emMax: number;
    setFrequency(options: { value: number; unit: 'Hz' | 'm' }): void;
    getFrequency(): number;
  }
  export interface Source { ra: number; dec: number }
  export interface Catalog {
    addSources(sources: Source[]): void;
    removeAll(): void;
  }
  export interface SkyClick {
    ra?: number;
    dec?: number;
    x: number;
    y: number;
    isDragging: boolean;
  }
  export interface Aladin {
    getRaDec(): ArrayLike<number>;
    getFov(): [number, number];
    getSize(): [number, number];
    gotoRaDec(ra: number, dec: number): void;
    setFoV(fov: number): void;
    setFoVRange(min: number, max: number): void;
    setProjection(projection: 'AIT'): void;
    setRotation(rotation: number): void;
    setCooGrid(options: { enabled?: boolean; color?: string; opacity?: number; labelSize?: number }): void;
    pix2world(x: number, y: number, frame?: 'ICRS'): [number, number] | undefined;
    world2pix(ra: number, dec: number, frame?: 'ICRS'): [number, number] | undefined;
    getBaseImageLayer(): HiPS | undefined;
    setBaseImageLayer(survey: HiPS | string): HiPS;
    addCatalog(catalog: Catalog): void;
    removeOverlay(catalog: Catalog): void;
    on(event: 'click', callback: ((event: SkyClick) => void) | undefined): void;
    on(event: 'positionChanged' | 'zoomChanged' | 'resizeChanged' | 'stackChanged', callback: (() => void) | undefined): void;
    // This npm build lacks the off/destroy APIs of some other releases.
    // Only the cleanup adapter accesses these version-specific internals.
    view: {
      resizeObserver: ResizeObserver;
      redrawClbk: FrameRequestCallback;
    };
  }
  const A: {
    init: Promise<void>;
    aladin(container: HTMLElement | string, options: {
      survey: HiPS;
      target: string;
      fov: number;
      projection: 'AIT';
      cooFrame: 'ICRS';
      showZoomControl: boolean;
      showLayersControl: boolean;
      showFullscreenControl: boolean;
      showFrame: boolean;
      showFov: boolean;
      showCooLocation: boolean;
      showProjectionControl: boolean;
      showStatusBar: boolean;
      showReticle: boolean;
      showCooGrid: boolean;
      gridColor?: string;
      gridOpacity?: number;
      backgroundColor: string;
      mode: 'dark';
      log: boolean;
      inertia: boolean;
    }): Aladin;
    HiPS(url: string, options: {
      name: string;
      imgFormat: 'png';
      successCallback: (layer: HiPS) => void;
      errorCallback: (error: unknown) => void;
    }): HiPS;
    catalog(options: {
      name: string;
      color: string;
      sourceSize: number;
      shape: 'cross';
    }): Catalog;
    source(ra: number, dec: number): Source;
  };
  export default A;
}
