const API_URL = import.meta.env.VITE_API_URL || '/api';

const TOKEN_KEY = 'streetscan_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function handleUnauthorized() {
  setToken(null);
  window.dispatchEvent(new Event('auth-expired'));
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (response.status === 401) handleUnauthorized();
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

// Media (<img>/<video>) cannot send Authorization headers, so protected files
// are fetched as blobs and served to the tag via an object URL.
export async function fetchMediaUrl(path: string): Promise<string> {
  const response = await fetch(`${API_URL}${path}`, { headers: authHeaders() });
  if (response.status === 401) handleUnauthorized();
  if (!response.ok) throw new Error(`media ${response.status}`);
  return URL.createObjectURL(await response.blob());
}

export { API_URL };
