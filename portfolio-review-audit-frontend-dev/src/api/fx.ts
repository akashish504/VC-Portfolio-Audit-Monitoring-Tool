import apiClient from '@/api/axios';

export type FxRateQuote = {
  quotecurrency: string;
  mid: number;
};

export type FxRatesResponse = {
  from: FxRateQuote;
  to: FxRateQuote[];
  timestamp: string;
  fy_end?: string;
  rate_date?: string;
};

export async function fetchFxRates(fromCurrency: string) {
  const res = await apiClient.get<FxRatesResponse>('/api/v1/fx/rates', {
    params: { from: fromCurrency.toUpperCase() },
  });
  return res.data;
}

export async function fetchFileFxPreviewRate(fileId: number, toCurrency: string) {
  const res = await apiClient.get<FxRatesResponse>(`/api/v1/files/${fileId}/fx/preview-rate`, {
    params: { to: toCurrency.toUpperCase() },
  });
  return res.data;
}
