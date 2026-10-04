// Summarises what a run did, for runs that report no confidence (e.g.
// meeting_action_items' extraction runs, which only record what was
// extracted). Shown in place of a percentage that would not mean anything.

export function describeActionsTaken(actionNames: string[]): string {
  const count = actionNames.length;
  if (count === 0) return "No new action items";

  const allCreates = actionNames.every((name) => name === "create_action_item");
  if (allCreates) {
    return `${count} action ${count === 1 ? "item" : "items"} created`;
  }
  return `${count} ${count === 1 ? "action" : "actions"} taken`;
}