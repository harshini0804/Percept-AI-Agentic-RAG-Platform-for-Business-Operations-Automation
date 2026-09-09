import { apiFetch } from "./client";

export interface ActionItemTrackerEntry {
  id: string;
  meeting_id: string;
  description: string;
  owner: string;
  deadline: string | null;
  status: string;
  nudge_count: number;
  escalated: boolean;
  is_recurring: boolean;
  created_at: string;
}

export function listTrackerItems(status?: string): Promise<ActionItemTrackerEntry[]> {
  const query = status ? `?status=${status}` : "";
  return apiFetch(`/meeting-action-items/tracker${query}`);
}

export function resolveActionItem(actionItemId: string): Promise<ActionItemTrackerEntry> {
  return apiFetch(`/meeting-action-items/${actionItemId}/resolve`, {
    method: "POST",
  });
}