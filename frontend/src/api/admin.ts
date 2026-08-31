import { apiFetch } from "./client";

export interface ResyncResponse {
  vertical: string;
  processed: string[];
  skipped: string[];
  errors: string[];
}

export interface SyncHistoryEntry {
  vertical: string;
  file_path: string;
  content_hash: string;
  last_synced_at: string;
}

export function resyncVertical(vertical: string): Promise<ResyncResponse> {
  return apiFetch(`/admin/resync/${vertical}`, { method: "POST" });
}

export function getSyncHistory(vertical?: string): Promise<SyncHistoryEntry[]> {
  const query = vertical ? `?vertical=${vertical}` : "";
  return apiFetch(`/admin/sync-history${query}`);
}