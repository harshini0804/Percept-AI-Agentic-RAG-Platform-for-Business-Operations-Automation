import { formatDecisionDetail } from "../utils/formatDecision";

// Shared recursive bullet renderer for any agent_decisions.detail (or
// similarly-shaped) object — used by RunDetail's Reasoning/Action
// panels and Escalations' pending_action summary, so every place that
// shows raw backend JSON gets identical, readable treatment from the
// same generic formatter (no per-vertical or per-page duplication).
export function DecisionBulletList({
  detail,
}: {
  detail: Record<string, unknown> | null | undefined;
}) {
  const bullets = formatDecisionDetail(detail);
  if (bullets.length === 0) return <p className="text-sm text-slate-400">No details available.</p>;
  return <BulletList bullets={bullets} />;
}

export function BulletList({ bullets }: { bullets: ReturnType<typeof formatDecisionDetail> }) {
  return (
    <ul className="text-sm text-slate-700 space-y-1.5">
      {bullets.map((b) => (
        <li key={b.key}>
          <span className="font-medium">{b.label}:</span> {b.value}
          {b.items && b.items.length > 0 && (
            <ul className="ml-5 mt-1.5 space-y-2">
              {b.items.map((group, i) => (
                <li key={i} className="border-l-2 border-slate-200 pl-3">
                  <BulletList bullets={group} />
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}