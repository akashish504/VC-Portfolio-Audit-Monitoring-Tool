type ToastPayload = { title?: string; description?: string };

function emit(level: 'success' | 'error' | 'info', message: string, payload?: ToastPayload) {
  // Lightweight placeholder toast to avoid changing app infra/deps.
  // Replace with your preferred toast system later (Sonner, etc).
  // eslint-disable-next-line no-console
  console[level === 'error' ? 'error' : 'log'](`[${level}] ${message}`, payload ?? '');
}

export const toast = {
  success: (message: string, payload?: ToastPayload) => emit('success', message, payload),
  error: (message: string, payload?: ToastPayload) => emit('error', message, payload),
  info: (message: string, payload?: ToastPayload) => emit('info', message, payload),
};

