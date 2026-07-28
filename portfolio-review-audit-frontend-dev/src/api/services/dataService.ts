import { BaseService } from './baseService';
import type { ApiResponse } from '../types';

export type DataSyncEnqueueResponse = { batch_id?: string };

/** POST /api/v1/data/process — mirrors portfolio-review-app-ui-master payload. */
class DataService extends BaseService {
  async syncData(): Promise<ApiResponse<DataSyncEnqueueResponse>> {
    return this.post<ApiResponse<DataSyncEnqueueResponse>>('/api/v1/data/process', {
      data: {
        migration_type: ['all'],
        manipulation_type: ['all'],
      },
    });
  }
}

export const dataService = new DataService();
