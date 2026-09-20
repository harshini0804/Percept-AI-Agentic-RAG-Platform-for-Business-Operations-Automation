// Single source of truth for vertical display names — previously
// duplicated as near-identical arrays in Submit.tsx, Escalations.tsx,
// Notifications.tsx, and Dashboard.tsx.

export const VERTICAL_LABELS: Record<string, string> = {
  dummy: "Dummy (Test Vertical)",
  post_incident: "Post-Incident",
  internal_mobility: "Internal Mobility & Skill-Gap Matching",
  contract_tracking: "Contract Obligation & Renewal Tracking",
  meeting_action_items: "Meeting Action Items",
};

export function getVerticalLabel(vertical: string): string {
  return VERTICAL_LABELS[vertical] ?? vertical;
}