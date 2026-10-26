import { apiFetch } from "./client";

export interface IntegrationEvent {
  id: string;
  source: string;
  delivery_id: string;
  event_type: string | null;
  status: string;
  summary: string | null;
  owner: string | null;
  run_id: string | null;
  received_at: string;
}

export function listIntegrationEvents(source?: string): Promise<IntegrationEvent[]> {
  const query = source ? `?source=${source}&limit=100` : "?limit=100";
  return apiFetch(`/integrations/events${query}`);
}
