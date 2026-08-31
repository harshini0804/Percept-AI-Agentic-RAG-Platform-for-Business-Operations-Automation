import { apiFetch } from "./client";

export interface EscalationSummary {
  id: string;
  run_id: string;
  vertical: string;
  reason: string | null;
  assigned_to: string | null;
  status: string;
  pending_action: { tool_name: string; arguments: Record<string, unknown> } | null;
  created_at: string;
}

export function listEscalations(vertical?: string): Promise<EscalationSummary[]> {
  const query = vertical ? `?vertical=${vertical}` : "";
  return apiFetch(`/escalations${query}`);
}

export function resolveEscalation(
  escalationId: string,
  approve: boolean,
  resolvedBy?: string
): Promise<EscalationSummary> {
  return apiFetch(`/escalations/${escalationId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ approve, resolved_by: resolvedBy || null }),
  });
}