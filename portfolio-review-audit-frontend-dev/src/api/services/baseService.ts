import apiClient, { ensureTokensInitialized } from '../axios';

export abstract class BaseService {
  protected async makeRequest<T>(
    requestFn: () => Promise<{ data: T }>,
    skipTokenCheck: boolean = false
  ): Promise<T> {
    if (!skipTokenCheck) {
      await ensureTokensInitialized();
    }
    const response = await requestFn();
    return response.data;
  }

  protected async get<T>(url: string, params?: unknown, skipTokenCheck = false): Promise<T> {
    return this.makeRequest<T>(
      () => apiClient.get<T>(url, { params, withCredentials: true }),
      skipTokenCheck
    );
  }

  protected async post<T>(url: string, data?: unknown, config?: Record<string, unknown>, skipTokenCheck = false): Promise<T> {
    return this.makeRequest<T>(
      () => apiClient.post<T>(url, data, { withCredentials: true, ...config }),
      skipTokenCheck
    );
  }

  protected async put<T>(url: string, data?: unknown, config?: Record<string, unknown>, skipTokenCheck = false): Promise<T> {
    return this.makeRequest<T>(
      () => apiClient.put<T>(url, data, { withCredentials: true, ...config }),
      skipTokenCheck
    );
  }

  protected async delete<T>(url: string, config?: Record<string, unknown>, skipTokenCheck = false): Promise<T> {
    return this.makeRequest<T>(
      () => apiClient.delete<T>(url, { withCredentials: true, ...config }),
      skipTokenCheck
    );
  }
}


// Utility function to convert JSON to FormData
export function jsonToFormData(json: Record<string, unknown>): FormData {
  const formData = new FormData();

  function appendFormData(data: unknown, rootName: string) {
    if (data instanceof File) {
      formData.append(rootName, data);
    } else if (Array.isArray(data)) {
      // Handle array of files specially
      if (rootName === 'attachments' && data.length > 0 && data[0] instanceof File) {
        data.forEach((file: File) => {
          formData.append('attachments', file);
        });
      } else {
        data.forEach((item, index) => {
          appendFormData(item, `${rootName}[${index}]`);
        });
      }
    } else if (typeof data === 'object' && data !== null) {
      Object.keys(data as Record<string, unknown>).forEach(key => {
        appendFormData((data as Record<string, unknown>)[key], `${rootName}[${key}]`);
      });
    } else if (data !== undefined && data !== null) {
      formData.append(rootName, String(data));
    }
  }

  Object.keys(json).forEach(key => {
    appendFormData(json[key], key);
  });

  return formData;
}