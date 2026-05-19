import axios from 'axios';
import { toast } from './toast';
import { browser } from '$app/environment';
import { env } from '$env/dynamic/public';

const api = axios.create({
  baseURL: env.PUBLIC_API_URL || 'http://localhost:8000'
});

api.interceptors.request.use((config) => {
  const token = browser ? localStorage.getItem('access_token') : null;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.config?.url?.includes('/login/token')) {
      return Promise.reject(error);
    }
    if (error.response?.status === 401 || error.response?.status === 403) {
      if (browser) {
        localStorage.removeItem('access_token');
      }
      toast.warning("Can't display all values to unauthenticated user, please log in!")
    }
    return Promise.reject(error);
  }
);

export default api;