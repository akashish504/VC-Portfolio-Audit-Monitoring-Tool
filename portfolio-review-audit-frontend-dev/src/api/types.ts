/** Minimal types for session bootstrap; extend as you add features. */

export type BatchProcessStatus =
  | 'FAILED'
  | 'IN_PROGRESS'
  | 'SUCCESS'
  | 'QUEUED'
  | string;

export interface User {
  soha_user: string;
  csrf_token: string;
  batch_process_status?: BatchProcessStatus;
}

export interface ApiResponse<T> {
  success: boolean;
  data: T;
  message?: string | null;
}
