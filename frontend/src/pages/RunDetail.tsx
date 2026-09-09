import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAgentRun } from "../api/agentRuns";

function ConfidenceBadge({ confidence }: { confidence: number | null }) {
  if (confidence === null) return <span className="text-slate-400">—</span>;

  const color =
    confidence >= 0.8 ? "bg-green-100 text-green-800" :
    confidence >= 0.5 ? "bg-amber-100 text-amber-800" :
    "bg-red-100 text-red-800";

  return (
    <span className={`px-2 py-1 rounded text-sm font-medium ${color}`}>
      {(confidence * 100).toFixed(0)}%
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

function RunDetail() {
  const { runId } = useParams<{ runId: string }>();

  const { data: run, isLoading, error } = useQuery({
    queryKey: ["agent-run", runId],
    queryFn: () => getAgentRun(runId!),
    enabled: !!runId,
  });

  if (isLoading) return <p>Loading run details...</p>;
  if (error) return <p className="text-red-600">Error: {(error as Error).message}</p>;
  if (!run) return null;

  const retrievalStep = run.decisions.find((d) => d.step_type === "retrieval");
  const reasoningStep = run.decisions.find((d) => d.step_type === "llm_reasoning");
  const actionStep = run.decisions.find((d) => d.step_type === "action");
  const escalationStep = run.decisions.find((d) => d.step_type === "escalation");

  return (
    <div className="max-w-3xl">
      <Link to="/" className="text-blue-600 hover:underline text-sm">← Back to Dashboard</Link>

      {/* Header */}
      <div className="bg-white rounded shadow p-6 mt-4 mb-4">
        <div className="flex justify-between items-start mb-2">
          <div>
            <h2 className="text-xl font-semibold">{run.vertical}</h2>
            <p className="text-sm text-slate-500">
              {run.trigger_type} · {new Date(run.created_at).toLocaleString()}
            </p>
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
            Top score: {(retrievalStep.detail?.top_score as number)?.toFixed(2) ?? "—"} ·{" "}
            {retrievalStep.detail?.num_results as number} result(s)
            {retrievalStep.detail?.retried ? " · retried once" : ""}
          </p>
        </div>
      )}

            {/* Reasoning panel — falls back to rendering the raw detail
          JSON when a vertical doesn't use the dummy's simple
          {content: string} shape (e.g. meeting_action_items logs
          {verdict, confidence} or {extracted_count, items}). */}
      {reasoningStep && (
        <div className="bg-white rounded shadow p-6 mb-4">
          <h3 className="font-medium mb-2">LLM Reasoning</h3>
          <pre className="text-sm text-slate-700 whitespace-pre-wrap bg-slate-50 p-3 rounded">
            {typeof reasoningStep.detail?.content === "string"
              ? (reasoningStep.detail.content as string)
              : JSON.stringify(reasoningStep.detail, null, 2)}
          </pre>
        </div>
      )}

      {/* Action-taken panel */}
      {actionStep && (
        <div className="bg-white rounded shadow p-6 mb-4 border-l-4 border-green-500">
          <h3 className="font-medium mb-2">Action Taken</h3>
          <p className="text-sm text-slate-700">
            <span className="font-mono bg-slate-100 px-1 rounded">
              {actionStep.detail?.action_name as string}
            </span>
          </p>
          <pre className="text-xs text-slate-500 mt-2">
            {JSON.stringify(actionStep.detail?.result, null, 2)}
          </pre>
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