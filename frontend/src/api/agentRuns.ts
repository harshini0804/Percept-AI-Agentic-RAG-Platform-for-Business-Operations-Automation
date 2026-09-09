import { apiFetch, apiFetchFormData } from "./client";

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
  role_matches: RoleMatchSummary[];
}

export interface RoleMatchSummary {
  id: string;
  rank: number;
  employee_name: string | null;
  department: string | null;
  rationale: string;
  confidence: number;
  notified: boolean;
  utilization_pct: number | null;
}

export interface AgentRunStats {
  total: number;
  completed: number;
  escalated: number;
  running: number;
  rejected: number;
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

export function getAgentRunStats(vertical?: string): Promise<AgentRunStats> {
  const query = vertical ? `?vertical=${vertical}` : "";
  return apiFetch(`/agent-runs/summary${query}`);
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
export function submitRunWithFile(vertical: string, file: File): Promise<SubmissionResponse> {
  const formData = new FormData();
  formData.append("vertical", vertical);
  formData.append("file", file);
  return apiFetchFormData(`/agent-runs/upload`, formData);
}