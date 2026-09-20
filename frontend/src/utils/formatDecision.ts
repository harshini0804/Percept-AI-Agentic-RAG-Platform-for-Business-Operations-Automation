// Generic formatter for agent_decisions.detail objects — turns raw
// JSON (whose shape differs per vertical: {confidence, should_act,
// reason} for dummy, {verdict, confidence} for meeting_action_items'
// Trigger 2, {extracted_count, items} for its Trigger 1, or a deeper
// structure like internal_mobility's {summary, candidates: [{...,
// skill_gaps: [...]}]}) into readable label/value bullets, WITHOUT
// needing per-vertical code.
//
// Fully recursive: an array of objects becomes a group of nested
// bullet-lists (one per item), and any field within those items —
// including another array, like a candidate's skill_gaps — recurses
// through the same logic. A shallow, one-level-deep version of this
// mangled internal_mobility's nested skill_gaps arrays into an
// unreadable comma string; this handles arbitrary nesting depth.
//
// Deliberately generic rather than per-vertical: a per-vertical
// formatter would need updating every time a vertical owner changes
// their JSON shape (this has already happened multiple times in this
// project) and silently breaks for any vertical built after it was
// written. This trades a small amount of phrasing polish for zero
// ongoing maintenance and working automatically for new verticals.

const FRIENDLY_KEY_LABELS: Record<string, string> = {
  confidence: "Confidence",
  verdict: "Outcome",
  should_act: "Should Act",
  should_create_ticket: "Should Create Ticket",
  reason: "Reason",
  owner: "Assigned To",
  description: "Description",
  deadline: "Deadline",
  is_recurring: "Recurring",
  recurring_from: "Recurring From",
  extracted_count: "Items Extracted",
  num_results: "Similar Cases Found",
  top_score: "Best Match Score",
  action_name: "Action",
  action_item_id: "Action Item ID",
  notified: "Notified",
  escalated: "Escalated",
  nudge_count: "Reminders Sent",
  content: "Details",
  analysis_summary: "Analysis",
  ticket_title: "Ticket Title",
  linked_incident_ids: "Linked Incidents",
  summary: "Summary",
  candidates: "Candidates",
  employee_id: "Employee ID",
  rank: "Rank",
  rationale: "Rationale",
  skill_gaps: "Skill Gaps",
};

function friendlyKey(key: string): string {
  if (FRIENDLY_KEY_LABELS[key]) return FRIENDLY_KEY_LABELS[key];
  return prettifySnakeCase(key);
}

// Exported so callers can prettify a standalone snake_case value
// (e.g. an action_name like "mark_resolved") the same way keys and
// enum-style values are prettified here.
export function prettifySnakeCase(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .split("_")
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

function formatPrimitive(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    // A non-integer between 0 and 1 reads as a confidence-style
    // score — render as a percentage rather than a bare decimal.
    if (value >= 0 && value <= 1 && !Number.isInteger(value)) {
      return `${Math.round(value * 100)}%`;
    }
    return String(value);
  }
  if (typeof value === "string" && /^[a-z]+(_[a-z]+)+$/.test(value)) {
    return prettifySnakeCase(value);
  }
  return String(value);
}

export interface DecisionBullet {
  key: string;
  label: string;
  value: string;
  // Present when this field's value was an array of objects — one
  // nested bullet-list per array item.
  items?: DecisionBullet[][];
}

export function capitalizeWords(value: string): string {
  return value
    .split(" ")
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

function formatField(key: string, value: unknown): DecisionBullet {
  const label = friendlyKey(key);

  if (value === null || value === undefined) {
    return { key, label, value: "—" };
  }

  if (key === "owner" && typeof value === "string") {
    // Owner names are deliberately stored lowercase in the database
    // (matching MANAGER_MAP keys and the case-sensitive owner filter
    // used in recurrence/follow-up matching) — capitalized here only
    // for display, never touching the underlying stored value.
    return { key, label, value: capitalizeWords(value) };
  }

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return { key, label, value: "None" };
    }
    if (typeof value[0] === "object" && value[0] !== null) {
      // Array of objects — each item recurses through this same
      // function, so any further nested arrays (e.g. a candidate's
      // skill_gaps) are handled correctly too, at any depth.
      const items = value.map((item) =>
        Object.entries(item as Record<string, unknown>)
          .filter(([, v]) => v !== null && v !== undefined)
          .map(([k, v]) => formatField(k, v))
      );
      const itemWord = value.length === 1 ? "item" : "items";
      return { key, label, value: `${value.length} ${itemWord}`, items };
    }
    // Array of primitives (e.g. a plain list of strings) — a simple
    // comma-joined line is sufficient here, no further nesting needed.
    return { key, label, value: value.map((v) => formatPrimitive(v)).join(", ") };
  }

  if (typeof value === "object") {
    // A nested single object — flatten its own fields as further
    // nested items under this one bullet, reusing the same
    // array-of-objects rendering path with a single-item array.
    const nestedFields = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => formatField(k, v));
    return { key, label, value: "", items: [nestedFields] };
  }

  return { key, label, value: formatPrimitive(value) };
}

export function formatDecisionDetail(
  detail: Record<string, unknown> | null | undefined
): DecisionBullet[] {
  if (!detail) return [];
  return Object.entries(detail)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([key, value]) => formatField(key, value));
}

// Attempts to parse a string as JSON, returning the parsed object
// only if it's a genuine plain object (not an array, not a
// primitive) — used to detect cases like internal_mobility's
// reasoning, which logs its entire structured ranking result as a
// JSON-formatted STRING under "content" rather than a real nested
// object the way every other vertical's shape does.
export function tryParseJsonObject(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
    return null;
  } catch {
    return null;
  }
}