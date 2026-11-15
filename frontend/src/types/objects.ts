export interface CatalogCandidate {
  name: string;
  object_type: string;
  type_code: string;
  ra: number;
  dec: number;
  separation_arcsec: number;
  match_basis: string;
  matched_ra: number;
  matched_dec: number;
  spectral_type: string | null;
  v_magnitude: number | null;
  parallax_mas: number | null;
  pm_ra_mas_yr: number | null;
  pm_dec_mas_yr: number | null;
  source_url: string;
}

export interface ObjectLookup {
  ra: number;
  dec: number;
  candidates: CatalogCandidate[];
  total_matches: number;
  search_limited: boolean;
  radius_arcsec: number;
  source: string;
  retrieved_at: string;
  cached: boolean;
  cache_warning: string | null;
}
