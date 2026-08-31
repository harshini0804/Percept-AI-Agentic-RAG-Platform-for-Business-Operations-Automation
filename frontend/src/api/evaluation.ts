import { apiFetch } from "./client";

export interface VerticalMetrics {
  vertical: string;
  total_runs: number;
  completed_count: number;
  escalated_count: number;
  resolution_rate: number;
  escalation_rate: number;
  avg_confidence: number | null;
  min_confidence: number | null;
  max_confidence: number | null;
  retrieval_retry_rate: number;
  tool_call_count: number;
  tool_calls_per_run: number;
}

export function getEvaluationMetrics(): Promise<VerticalMetrics[]> {
  return apiFetch(`/evaluation/metrics`);
}