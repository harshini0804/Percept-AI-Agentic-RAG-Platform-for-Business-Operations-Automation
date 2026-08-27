/**
 * Shared Agent Contract (Section 3.2, Final Implementation Plan)
 *
 * Mirrors backend/app/schemas/agent_contract.py exactly. Used by the
 * report/result viewer and dashboard, which render every vertical's
 * output inside the same shared shell.
 */

export const Vertical = {
  POST_INCIDENT: "post_incident",
  INTERNAL_MOBILITY: "internal_mobility",
  CONTRACT_TRACKING: "contract_tracking",
  MEETING_ACTION_ITEMS: "meeting_action_items",
} as const;
export type Vertical = (typeof Vertical)[keyof typeof Vertical];

export const TriggerType = {
  UPLOAD: "upload",
  SCHEDULED_INGESTION: "scheduled_ingestion",
  SCHEDULED_FOLLOWUP: "scheduled_followup",
  MANUAL_RESYNC: "manual_resync",
} as const;
export type TriggerType = (typeof TriggerType)[keyof typeof TriggerType];

export interface ActionTaken {
  action_name: string;
  target_id?: string | null;
  detail?: Record<string, unknown> | null;
}

export interface AgentRunInput {
  vertical: Vertical;
  trigger_type: TriggerType;
  input_document_id?: string | null;
  input_payload?: Record<string, unknown> | null;
}

export interface AgentRunOutput {
  status: string;
  confidence: number; // 0.0–1.0
  actions_taken: ActionTaken[];
  escalated: boolean;
  escalation_reason?: string | null;
}