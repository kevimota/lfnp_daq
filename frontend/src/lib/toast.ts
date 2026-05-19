import { writable } from 'svelte/store';
import { browser } from '$app/environment';

interface Toast {
  id: string;
  message: string;
  type: 'info' | 'success' | 'warning' | 'error';
  duration: number;
}

function createToastStore() {
  const { subscribe, update } = writable<Toast[]>([]);

  return {
    subscribe,

    show(message: string, type: 'info' | 'success' | 'warning' | 'error' = 'info', duration: number = 5000) {
      if (!browser) return;

      const id = crypto.randomUUID();
      const newToast = { id, message, type, duration };

      update(toasts => [...toasts, newToast]);

      if (duration > 0) {
        setTimeout(() => {
          this.remove(id);
        }, duration);
      }
    },

    remove(id: string) {
      if (!browser) return;
      update(toasts => toasts.filter(t => t.id !== id));
    },

    success(message: string, duration?: number) {
      this.show(message, 'success', duration);
    },

    error(message: string, duration?: number) {
      this.show(message, 'error', duration);
    },

    info(message: string, duration?: number) {
      this.show(message, 'info', duration);
    },

    warning(message: string, duration?: number) {
      this.show(message, 'warning', duration);
    }
  };
}

export const toast = createToastStore();