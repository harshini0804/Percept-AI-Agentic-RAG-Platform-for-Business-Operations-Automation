import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listEscalations, resolveEscalation } from "../api/escalations";

function Escalations() {
  const queryClient = useQueryClient();

  const { data: escalations, isLoading, error } = useQuery({
    queryKey: ["escalations"],
    queryFn: () => listEscalations(),
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
  if (error) return <p className="text-red-600">Error: {(error as Error).message}</p>;

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Escalation Queue</h2>

      {escalations?.length === 0 && (
        <p className="text-slate-500">No open escalations. Nothing needs review right now.</p>
      )}

      <div className="flex flex-col gap-4">
        {escalations?.map((esc) => (
          <div key={esc.id} className="bg-white rounded shadow p-5">
            <div className="flex justify-between items-start mb-2">
              <div>
                <span className="text-xs uppercase tracking-wide text-slate-400">{esc.vertical}</span>
                <p className="text-sm text-slate-700 mt-1">{esc.reason}</p>
              </div>
              <Link to={`/runs/${esc.run_id}`} className="text-blue-600 hover:underline text-sm">
                View run
              </Link>
            </div>

            {esc.pending_action ? (
              <p className="text-xs text-slate-500 mb-3">
                Pending action: <span className="font-mono">{esc.pending_action.tool_name}</span>
              </p>
            ) : (
              <p className="text-xs text-slate-400 mb-3">No action was proposed for this case.</p>
            )}

            <div className="flex gap-2">
              <button
                onClick={() => mutation.mutate({ id: esc.id, approve: true })}
                disabled={mutation.isPending || !esc.pending_action}
                className="bg-green-600 text-white text-sm px-3 py-1.5 rounded disabled:opacity-50"
              >
                Approve
              </button>
              <button
                onClick={() => mutation.mutate({ id: esc.id, approve: false })}
                disabled={mutation.isPending}
                className="bg-red-100 text-red-700 text-sm px-3 py-1.5 rounded disabled:opacity-50"
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