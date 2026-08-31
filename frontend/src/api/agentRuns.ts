import { apiFetch } from "./client";

export interface AgentRunSummary {
  id: string;
  vertical: string;
  trigger_type: string;
  status: string;
  confidence: number | null;
  created_at: string;
}

export interface AgentDecisionDetail {
  id: string;
  step_type: string;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface AgentRunDetail extends AgentRunSummary {
  decisions: AgentDecisionDetail[];
}

export interface SubmissionResponse {
  run_id: string;
  status: string;
  confidence: number;
  escalated: boolean;
}

export function listAgentRuns(vertical?: string): Promise<AgentRunSummary[]> {
  const query = vertical ? `?vertical=${vertical}` : "";
  return apiFetch(`/agent-runs${query}`);
}

export function getAgentRun(runId: string): Promise<AgentRunDetail> {
  return apiFetch(`/agent-runs/${runId}`);
}

export function submitRun(vertical: string, inputText: string): Promise<SubmissionResponse> {
  return apiFetch(`/agent-runs`, {
    method: "POST",
    body: JSON.stringify({ vertical, input_text: inputText }),
  });
}