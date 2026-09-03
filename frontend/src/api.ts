import type { Category, Item, Priority, RegulationCase, Source, Stats } from "./types";

const request = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Ошибка API: ${response.status}`);
  }
  return response.status === 204 ? (undefined as T) : response.json();
};

export interface Filters {
  q?: string;
  published_date?: string;
  source?: string;
  category?: Category | "";
  priority?: Priority | "";
  entity_type?: "news" | "regulation" | "";
  review_required?: "true" | "false" | "";
}

export const api = {
  items: (filters: Filters = {}) => {
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value) as string[][]);
    return request<Item[]>(`/api/items?${query}`);
  },
  item: (id: string) => request<Item>(`/api/items/${id}`),
  stats: () => request<Stats>("/api/stats"),
  sources: () => request<Source[]>("/api/sources"),
  regulations: () => request<RegulationCase[]>("/api/regulations"),
  loadDemo: () => request<{ accepted: number; item_ids: string[] }>("/api/demo/load", { method: "POST" }),
  analyzeBatch: () => request<{ total: number; succeeded: number }>("/api/items/analyze-batch", { method: "POST", body: JSON.stringify({ limit: 20 }) }),
  analyze: (id: string, again = false) => request<Item>(`/api/items/${id}/${again ? "reanalyze" : "analyze"}`, { method: "POST" }),
  patchItem: (id: string, data: unknown) => request<Item>(`/api/items/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  hideItem: (id: string) => request(`/api/items/${id}/hide`, { method: "POST" }),
  createManual: (data: unknown) => request<Item>("/api/items/manual", { method: "POST", body: JSON.stringify(data) }),
  createSource: (data: unknown) => request<Source>("/api/sources", { method: "POST", body: JSON.stringify(data) }),
  patchSource: (id: string, data: unknown) => request<Source>(`/api/sources/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteSource: (id: string) => request<void>(`/api/sources/${id}`, { method: "DELETE" }),
};
