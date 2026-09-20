import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import { listEscalations, resolveEscalation } from "../api/escalations";
import { getVerticalLabel, VERTICAL_LABELS } from "../utils/verticals";
import { DecisionBulletList } from "../components/DecisionBullets";

const VERTICAL_OPTIONS = [
  { value: "", label: "All verticals" },
  ...Object.entries(VERTICAL_LABELS).map(([value, label]) => ({ value, label })),
];

function daysElapsed(createdAt: string): string {
  const created = new Date(createdAt).getTime();
  const now = Date.now();
  const diffMs = now - created;
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return "today";
  if (diffDays === 1) return "1 day ago";
  return `${diffDays} days ago`;
}

function Escalations() {
  const [vertical, setVertical] = useState("");
  const queryClient = useQueryClient();

  const { data: escalations, isLoading, error } = useQuery({
    queryKey: ["escalations", vertical],
    queryFn: () => listEscalations(vertical || undefined),
  });

  const mutation = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      resolveEscalation(id, approve, "reviewer"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["escalations"] });
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
  });

  if (isLoading) return <p>Loading escalations...</p>;
  if (error) return <p className="text-red-600">Something went wrong loading escalations. ({(error as Error).message})</p>;

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">Escalation Queue</h2>
        <select
          value={vertical}
          onChange={(e) => setVertical(e.target.value)}
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white"
        >
          {VERTICAL_OPTIONS.map((v) => (
            <option key={v.value} value={v.value}>
              {v.label}
            </option>
          ))}
        </select>
      </div>

      {escalations?.length === 0 && (
        <p className="text-slate-500">No open escalations. Nothing needs review right now.</p>
      )}

      <div className="flex flex-col gap-4">
        {escalations?.map((esc) => (
          <div
            key={esc.id}
            className="bg-white rounded-xl shadow-md border border-slate-200 p-5"
          >
            <div className="flex justify-between items-start mb-2">
              <div>
                <span className="text-xs uppercase tracking-wide text-slate-400">
                  {getVerticalLabel(esc.vertical)}
                </span>
                <p className="text-sm text-slate-700 mt-1">{esc.reason}</p>
                <div className="flex items-center gap-2 mt-1.5">
                  <span className="text-xs text-slate-400">
                    {new Date(esc.created_at).toLocaleString()}
                  </span>
                  <span className="text-xs text-slate-300">·</span>
                  <span className="text-xs text-slate-400">{daysElapsed(esc.created_at)}</span>
                </div>
              </div>
              <Link
                to={`/runs/${esc.run_id}`}
                title="View run"
                className="text-slate-400 hover:text-blue-600 hover:bg-slate-100 rounded-full p-1.5 transition-colors"
              >
                <ExternalLink size={16} />
              </Link>
            </div>

            {esc.pending_action ? (
              <div className="bg-slate-50 rounded-lg p-3 mb-3">
                <p className="text-xs font-medium text-slate-500 mb-1.5">
                  Pending action: {esc.pending_action.tool_name}
                </p>
                <DecisionBulletList detail={esc.pending_action.arguments} />
              </div>
            ) : (
              <p className="text-xs text-slate-400 mb-3">No action was proposed for this case.</p>
            )}

            <div className="flex gap-2">
              <button
                onClick={() => mutation.mutate({ id: esc.id, approve: true })}
                disabled={mutation.isPending || !esc.pending_action}
                title={!esc.pending_action ? "No pending action to approve for this case" : undefined}
                className="bg-green-600 hover:bg-green-700 text-white text-xs font-medium px-3 py-1 rounded-lg cursor-pointer transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-green-600"
              >
                Approve
              </button>
              <button
                onClick={() => mutation.mutate({ id: esc.id, approve: false })}
                disabled={mutation.isPending}
                className="bg-red-600 hover:bg-red-700 text-white text-xs font-medium px-3 py-1 rounded-lg cursor-pointer transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-red-600"
              >
                Reject
              </button>
            </div>
          </div>
        ))}
      </div>

      {mutation.isError && (
        <p className="text-red-600 text-sm mt-4">{(mutation.error as Error).message}</p>
      )}
    </div>
  );
}

export default Escalations;