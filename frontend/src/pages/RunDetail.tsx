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

      {/* Reasoning panel */}
      {reasoningStep && (
        <div className="bg-white rounded shadow p-6 mb-4">
          <h3 className="font-medium mb-2">LLM Reasoning</h3>
          <pre className="text-sm text-slate-700 whitespace-pre-wrap bg-slate-50 p-3 rounded">
            {reasoningStep.detail?.content as string}
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