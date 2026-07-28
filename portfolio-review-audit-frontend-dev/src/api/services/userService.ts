import { BaseService } from './baseService';
import { ApiResponse, User } from '../types';

class UserService extends BaseService {
  private inflight?: Promise<ApiResponse<User>>;

  async getUser(): Promise<ApiResponse<User>> {
    if (this.inflight) return this.inflight;

    this.inflight = this.get<ApiResponse<User>>('/api/v1/user/get-user', undefined, true)
      .finally(() => { this.inflight = undefined; });
    return this.inflight;
  }
}

export const userService = new UserService();
