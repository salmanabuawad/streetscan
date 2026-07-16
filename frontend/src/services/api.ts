const API_URL = import.meta.env.VITE_API_URL || '/api';

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export { API_URL };
