import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { getAgentRun } from "../api/agentRuns";
import { getVerticalLabel } from "../utils/verticals";
import { formatRunMeta } from "../utils/formatDate";
import { formatConfidence, describeRetrievalScore } from "../utils/formatScore";
import { formatDecisionDetail, prettifySnakeCase, tryParseJsonObject  } from "../utils/formatDecision";

function ConfidenceBadge({ confidence }: { confidence: number | null }) {
  if (confidence === null) return <span className="text-slate-400">—</span>;

  const color =
    confidence >= 0.8 ? "bg-green-100 text-green-800" :
    confidence >= 0.5 ? "bg-amber-100 text-amber-800" :
    "bg-red-100 text-red-800";

  return (
    <span className={`px-2 py-1 rounded text-sm font-medium ${color}`}>
      {formatConfidence(confidence)}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color =
    status === "completed" ? "bg-green-100 text-green-800" :
    status === "escalated" ? "bg-amber-100 text-amber-800" :
    "bg-slate-100 text-slate-600";

  return <span className={`px-2 py-1 rounded text-sm font-medium ${color}`}>{status}</span>;
}

// Vertical 2 availability badge (Section 8.2): capacity is a signal,
// never a filter — even a fully-busy candidate stays on the board.
function CapacityBadge({ utilization }: { utilization: number | null }) {
  if (utilization === null) return null;
  return utilization < 100 ? (
    <span className="px-2 py-1 rounded text-xs font-medium bg-blue-100 text-blue-800">
      {utilization}% busy
    </span>
  ) : (
    <span className="px-2 py-1 rounded text-xs font-medium bg-red-100 text-red-800">
      Full capacity
    </span>
  );
}

// Vertical 3 reminder badge (Section 8.3): whether this specific
// obligation was auto-scheduled (confidence cleared the action
// threshold) or flagged for manual review — a single contract can
// have both outcomes among its obligations, so this is per-row, not
// a run-level status.
function ReminderBadge({ created }: { created: boolean }) {
  return created ? (
    <span className="px-2 py-1 rounded text-xs font-medium bg-green-100 text-green-800">
      Reminder set
    </span>
  ) : (
    <span className="px-2 py-1 rounded text-xs font-medium bg-amber-100 text-amber-800">
      Needs review
    </span>
  );
}

// Renders a formatDecisionDetail() bullet list — shared by the
// Reasoning and Action Taken panels so both get identical treatment.
function DecisionBulletList({ detail }: { detail: Record<string, unknown> | null | undefined }) {
  const bullets = formatDecisionDetail(detail);
  if (bullets.length === 0) return <p className="text-sm text-slate-400">No details available.</p>;
  return <BulletList bullets={bullets} />;
}

function BulletList({ bullets }: { bullets: ReturnType<typeof formatDecisionDetail> }) {
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

function RunDetail() {
  const { runId } = useParams<{ runId: string }>();

  const { data: run, isLoading, error } = useQuery({
    queryKey: ["agent-run", runId],
    queryFn: () => getAgentRun(runId!),
    enabled: !!runId,
  });

  if (isLoading) return <p>Loading run details...</p>;
  if (error) return <p className="text-red-600">Something went wrong loading this run. ({(error as Error).message})</p>;
  if (!run) return null;

  const retrievalStep = run.decisions.find((d) => d.step_type === "retrieval");
  const reasoningStep = run.decisions.find((d) => d.step_type === "llm_reasoning");
  const actionStep = run.decisions.find((d) => d.step_type === "action");
  const escalationStep = run.decisions.find((d) => d.step_type === "escalation");

  return (
    <div className="max-w-3xl mx-auto">
      <Link
        to="/"
        className="inline-flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-900 bg-white border border-slate-200 rounded-lg px-3 py-1.5 shadow-sm hover:shadow transition-all mb-4"
      >
        <ArrowLeft size={14} />
        Back to Dashboard
      </Link>

      {/* Header */}
      <div className="bg-white rounded shadow p-6 mb-4">
        <div className="flex justify-between items-start mb-2">
          <div>
            <h2 className="text-xl font-semibold">{getVerticalLabel(run.vertical)}</h2>
            <p className="text-sm text-slate-500">{formatRunMeta(run.trigger_type, run.created_at)}</p>
          </div>
          <div className="flex gap-2 items-center">
            <StatusBadge status={run.status} />
            <ConfidenceBadge confidence={run.confidence} />
          </div>
        </div>
      </div>

      {/* Retrieved context panel */}
      {retrievalStep && (
        <div className="bg-white rounded shadow p-6 mb-4">
          <h3 className="font-medium mb-2">Retrieved Context</h3>
          <p className="text-sm text-slate-600">
            {describeRetrievalScore(
              retrievalStep.detail?.top_score as number | null,
              retrievalStep.detail?.num_results as number | null
            )}
            {retrievalStep.detail?.retried ? " · retried once" : ""}
          </p>
        </div>
      )}

      {/* Reasoning panel — a plain string "content" (the dummy
          vertical's shape) renders as prose; anything else renders
          as generic label/value bullets via formatDecisionDetail,
          which works for any vertical's JSON shape with no
          per-vertical code. */}
      {reasoningStep && (
        <div className="bg-white rounded shadow p-6 mb-4">
          <h3 className="font-medium mb-2">LLM Reasoning</h3>
          {(() => {
            const content = reasoningStep.detail?.content;
            if (typeof content === "string") {
              const parsed = tryParseJsonObject(content);
              if (parsed) {
                return <DecisionBulletList detail={parsed} />;
              }
              return (
                <p className="text-sm text-slate-700 whitespace-pre-wrap">{content}</p>
              );
            }
            return <DecisionBulletList detail={reasoningStep.detail as Record<string, unknown>} />;
          })()}
        </div>
      )}

      {/* Action-taken panel */}
      {actionStep && (
        <div className="bg-white rounded shadow p-6 mb-4 border-l-4 border-green-500">
          <h3 className="font-medium mb-2">Action Taken</h3>
          <p className="text-sm text-slate-700 mb-2">
            <span className="font-medium">
              {prettifySnakeCase(actionStep.detail?.action_name as string | undefined)}
            </span>
          </p>
          <DecisionBulletList detail={actionStep.detail?.result as Record<string, unknown>} />
        </div>
      )}

      {/* Internal Mobility leaderboard (Vertical 2, Section 8.2) */}
      {run.vertical === "internal_mobility" && run.role_matches.length > 0 && (
        <div className="bg-white rounded shadow p-6 mb-4">
          <h3 className="font-medium mb-3">Ranked Candidates</h3>
          <div className="space-y-3">
            {run.role_matches.map((m) => (
              <div key={m.id} className="border border-slate-200 rounded-lg p-3">
                <div className="flex items-center gap-3 mb-1 flex-wrap">
                  <span className="w-6 h-6 rounded-full bg-slate-900 text-white text-xs flex items-center justify-center">
                    {m.rank}
                  </span>
                  <span className="font-medium">{m.employee_name ?? "—"}</span>
                  {m.department && (
                    <span className="text-xs text-slate-500">{m.department}</span>
                  )}
                  <ConfidenceBadge confidence={m.confidence} />
                  <CapacityBadge utilization={m.utilization_pct} />
                  {m.notified ? (
                    <span className="px-2 py-1 rounded text-xs font-medium bg-green-100 text-green-800">
                      Notified
                    </span>
                  ) : (
                    <span className="px-2 py-1 rounded text-xs font-medium bg-slate-100 text-slate-600">
                      Listed (no alert)
                    </span>
                  )}
                </div>
                {m.rationale && (
                  <p className="text-sm text-slate-700 mt-1">{m.rationale}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Contract obligation timeline (Vertical 3, Section 8.3): a
          structured table, not a single reasoning/action panel — one
          contract can have several obligations, each independently
          auto-scheduled or flagged (Section 8.3, Agentic Decision
          Points), so this renders every obligation from the run, not
          just the first decision of a given step_type the way the
          generic panels above do. */}
      {run.vertical === "contract_tracking" && run.obligations.length > 0 && (
        <div className="bg-white rounded shadow p-6 mb-4">
          <h3 className="font-medium mb-3">Extracted Obligations</h3>
          <div className="space-y-3">
            {run.obligations.map((o) => (
              <div key={o.id} className="border border-slate-200 rounded-lg p-3">
                <div className="flex items-center gap-3 mb-1 flex-wrap">
                  <span className="font-medium">{o.description}</span>
                  {o.type && (
                    <span className="text-xs text-slate-500">{o.type}</span>
                  )}
                  <ConfidenceBadge confidence={o.confidence} />
                  <ReminderBadge created={o.reminder_created} />
                </div>
                {o.obligation_date && (
                  <p className="text-sm text-slate-500 mt-1">
                    Due: {new Date(o.obligation_date).toLocaleDateString()}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Escalation panel */}
      {escalationStep && (
        <div className="bg-white rounded shadow p-6 mb-4 border-l-4 border-amber-500">
          <h3 className="font-medium mb-2">Escalated for Human Review</h3>
          <p className="text-sm text-slate-700">{escalationStep.detail?.reason as string}</p>
        </div>
      )}
    </div>
  );
}

export default RunDetail;