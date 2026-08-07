// src/api.ts
import axios from 'axios';

const api = axios.create({
  baseURL: '/api', // Vite dev server proxy forwards to backend
  timeout: 60000,
});

export interface CompareRequest {
  reference_url: string;
  live_url: string;
  categories: string[];
  all_annotations: boolean;
}

export interface CompareResponse {
  run_id: string;
}

export interface StatusResponse {
  status: 'pending' | 'running' | 'done' | 'failed';
}

export interface ResultResponse {
  run_id: string;
  reference_url: string;
  live_url: string;
  results: Record<string, {
    report: unknown;
    screenshots: {
      reference?: string;
      live?: string;
      annotated?: string;
    };
    annotations: unknown[];
  }>;
}

export const postCompare = (data: CompareRequest) =>
  api.post<CompareResponse>('/compare', data);

export const getStatus = (runId: string) =>
  api.get<StatusResponse>(`/compare/${runId}`);

export const getResults = (runId: string) =>
  api.get<ResultResponse>(`/compare/${runId}/results`);
export default api;
