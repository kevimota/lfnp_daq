import { writable, derived } from 'svelte/store';
import { toast } from './toast';
import { goto } from '$app/navigation';
import { browser } from '$app/environment';
import api from './api';

interface UserInfo {
  id: string;
  username: string;
  full_name: string | null;
  email: string;
}

function createAuthStore() {
  const storedToken = browser ? localStorage.getItem('access_token') : null;

  const token = writable<string | null>(storedToken);
  const userInfo = writable<UserInfo | null>(null);

  function handleUnauthorized() {
    token.set(null);
    userInfo.set(null);
    if (browser) {
      localStorage.removeItem('access_token');
    }
    toast.warning('Your session has expired. Please log in again.');
    goto('/');
  }

  async function fetchUserInfo() {
    try {
      const { data } = await api.get('/users/me');
      userInfo.set(data);
    } catch {
      // interceptor handles 401 redirect
    }
  }

  async function validateTokenOnInit() {
    if (!browser) return;
    const storedToken = localStorage.getItem('access_token');
    if (!storedToken) return;

    try {
      const { data } = await api.get('/users/me');
      userInfo.set(data);
      token.set(storedToken);
    } catch {
      localStorage.removeItem('access_token');
      token.set(null);
      toast.warning('Your session has expired. Please log in again.');
    }
  }

  return {
    token,
    userInfo,
    handleUnauthorized,

    async login(username: string, password: string): Promise<{ success: boolean; error?: string }> {
      const formData = new URLSearchParams();
      formData.append('username', username);
      formData.append('password', password);

      try {
        const { data } = await api.post('/login/token', formData, {
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        });

        token.set(data.access_token);
        localStorage.setItem('access_token', data.access_token);
        await fetchUserInfo();
        toast.success('Login successful');
        return { success: true };
      } catch (error: any) {
        if (error.response?.status === 401) {
          toast.error('Incorrect username or password');
          return { success: false, error: 'Incorrect username or password' };
        }
        toast.error('Connection error. Please try again.');
        return { success: false, error: 'Connection error. Please try again.' };
      }
    },

    logout() {
      token.set(null);
      userInfo.set(null);
      if (browser) {
        localStorage.removeItem('access_token');
      }
      toast.info('User logged out');
    },

    init() {
      validateTokenOnInit();
    }
  };
}

export const auth = createAuthStore();
export const isLoggedIn = derived(auth.token, ($token) => $token !== null);
export const userInfo = auth.userInfo;

export function getInitials(fullName: string | null): string {
  if (!fullName) return 'U';
  const parts = fullName.trim().split(/\s+/);
  if (parts.length === 1) {
    return parts[0].charAt(0).toUpperCase();
  }
  return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
}
