// Shared date-label formatting — replaces raw "upload · <date>" style
// strings with a properly worded line.

const TRIGGER_TYPE_LABELS: Record<string, string> = {
  upload: "Submitted",
  scheduled_ingestion: "Scheduled Ingestion",
  scheduled_followup: "Scheduled Follow-up",
  manual_resync: "Manual Resync",
};

export function formatTriggerType(triggerType: string): string {
  return TRIGGER_TYPE_LABELS[triggerType] ?? triggerType;
}

// For a run's header line: "Submitted on Sep 20, 2026, 4:12 PM"
export function formatRunMeta(triggerType: string, isoDate: string): string {
  return `${formatTriggerType(triggerType)} on ${new Date(isoDate).toLocaleString()}`;
}

// Generic version for any other "<Prefix> on <date>" label, e.g.
// Dashboard's "Created On" column or Tracker's "Created" line.
export function formatDateLabel(prefix: string, isoDate: string): string {
  return `${prefix} on ${new Date(isoDate).toLocaleString()}`;
}