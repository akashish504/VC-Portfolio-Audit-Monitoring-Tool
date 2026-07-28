/**
 * Review cycle + cycle adjustments (entries & logs) backed by portfolio-review-audit-backend:
 * - GET/POST /api/v1/review-cycles
 * - GET/POST/PATCH /api/v1/config (key: review_cycle_adjustments)
 */
import apiClient from './axios';
import type { ReviewCompanyEntry, ReviewCycle, ReviewCycleLog } from '@/types/reviewCycle';

const CONFIG_KEY = 'review_cycle_adjustments';
const DEFAULT_REVIEW_CYCLES_TTL_MS = 5 * 60 * 1000;

let _reviewCyclesCache: ReviewCycle[] | null = null;
let _reviewCyclesInFlight: Promise<ReviewCycle[]> | null = null;
let _reviewCyclesCachedAt = 0;

export interface Page<T> {
  items: T[];
  total: number;
}

export interface ApiReviewCycle {
  id: string;
  name: string | null;
  status: string | null;
  starts_at: string | null;
  ends_at: string | null;
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ApiConfigRow {
  id: number;
  key: string;
  value: Record<string, unknown>;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface CycleAdjustmentsStored {
  entries: ReviewCompanyEntry[];
  logs: ReviewCycleLog[];
}

function mapApiCycleToUi(c: ApiReviewCycle): ReviewCycle {
  const label =
    (typeof c.name === 'string' && c.name.trim() !== ''
      ? c.name
      : (c.meta?.label as string | undefined)) || c.id;
  return {
    id: c.id,
    label,
    createdAt: c.created_at,
    startsAt: c.starts_at,
    endsAt: c.ends_at,
  };
}

export async function fetchReviewCycles(): Promise<ReviewCycle[]> {
  const now = Date.now();
  if (_reviewCyclesCache && now - _reviewCyclesCachedAt < DEFAULT_REVIEW_CYCLES_TTL_MS) {
    return _reviewCyclesCache;
  }
  if (_reviewCyclesInFlight) return _reviewCyclesInFlight;

  _reviewCyclesInFlight = (async () => {
    const { data } = await apiClient.get<Page<ApiReviewCycle>>('/api/v1/review-cycles', {
      params: { limit: 200, offset: 0 },
    });
    const items = data.items ?? [];
    const cycles = items
      .map(mapApiCycleToUi)
      .sort((a, b) => {
        const aTime = a.startsAt ? new Date(a.startsAt).getTime() : 0;
        const bTime = b.startsAt ? new Date(b.startsAt).getTime() : 0;
        if (bTime !== aTime) return bTime - aTime;
        return b.id.localeCompare(a.id);
      });
    _reviewCyclesCache = cycles;
    _reviewCyclesCachedAt = Date.now();
    return cycles;
  })();

  try {
    return await _reviewCyclesInFlight;
  } finally {
    _reviewCyclesInFlight = null;
  }
}

export function clearReviewCyclesCache() {
  _reviewCyclesCache = null;
  _reviewCyclesCachedAt = 0;
  _reviewCyclesInFlight = null;
}

export async function deleteReviewCycleApi(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/review-cycles/${encodeURIComponent(id)}`);
}

export async function fetchCycleAdjustmentsConfig(): Promise<{
  configId: number | null;
  entries: ReviewCompanyEntry[];
  logs: ReviewCycleLog[];
}> {
  const { data } = await apiClient.get<Page<ApiConfigRow>>('/api/v1/config', {
    params: { limit: 500, offset: 0 },
  });
  const row = (data.items ?? []).find((r) => r.key === CONFIG_KEY);
  if (!row) {
    return { configId: null, entries: [], logs: [] };
  }
  const v = row.value as unknown as Partial<CycleAdjustmentsStored>;
  return {
    configId: row.id,
    entries: Array.isArray(v.entries) ? v.entries : [],
    logs: Array.isArray(v.logs) ? v.logs : [],
  };
}

export async function ensureCycleAdjustmentsConfig(
  entries: ReviewCompanyEntry[],
  logs: ReviewCycleLog[],
): Promise<number> {
  const current = await fetchCycleAdjustmentsConfig();
  const payload: CycleAdjustmentsStored = { entries, logs };
  if (current.configId != null) {
    await apiClient.patch(`/api/v1/config/${current.configId}`, {
      value: payload as unknown as Record<string, unknown>,
    });
    return current.configId;
  }
  const { data } = await apiClient.post<ApiConfigRow>('/api/v1/config', {
    key: CONFIG_KEY,
    value: payload as unknown as Record<string, unknown>,
    description: 'Review cycle company entries and audit logs (frontend)',
  });
  return data.id;
}

export async function saveCycleAdjustmentsConfig(
  configId: number | null,
  entries: ReviewCompanyEntry[],
  logs: ReviewCycleLog[],
): Promise<number> {
  const payload: CycleAdjustmentsStored = { entries, logs };
  if (configId != null) {
    await apiClient.patch(`/api/v1/config/${configId}`, {
      value: payload as unknown as Record<string, unknown>,
    });
    return configId;
  }
  return ensureCycleAdjustmentsConfig(entries, logs);
}
