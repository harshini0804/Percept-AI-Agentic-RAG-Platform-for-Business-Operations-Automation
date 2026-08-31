import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listAgentRuns } from "../api/agentRuns";

function Dashboard() {
  const { data: runs, isLoading, error } = useQuery({
    queryKey: ["agent-runs"],
    queryFn: () => listAgentRuns(),
  });

  if (isLoading) return <p>Loading runs...</p>;
  if (error) return <p className="text-red-600">Error: {(error as Error).message}</p>;

  const total = runs?.length ?? 0;
  const completed = runs?.filter((r) => r.status === "completed").length ?? 0;
  const escalated = runs?.filter((r) => r.status === "escalated").length ?? 0;
  const running = runs?.filter((r) => r.status === "running").length ?? 0;

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Dashboard</h2>

      {/* Vital metric cards */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Total Runs</p>
          <p className="text-2xl font-semibold mt-1">{total}</p>
        </div>
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Completed</p>
          <p className="text-2xl font-semibold mt-1 text-green-700">{completed}</p>
        </div>
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Escalated</p>
          <p className="text-2xl font-semibold mt-1 text-amber-700">{escalated}</p>
        </div>
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">In Progress</p>
          <p className="text-2xl font-semibold mt-1 text-slate-500">{running}</p>
        </div>
      </div>

      <h3 className="font-medium mb-3">Recent Agent Runs</h3>
      <table className="w-full bg-white rounded shadow">
        <thead>
          <tr className="text-left border-b">
            <th className="p-3">Vertical</th>
            <th className="p-3">Status</th>
            <th className="p-3">Confidence</th>
            <th className="p-3">Created</th>
            <th className="p-3"></th>
          </tr>
        </thead>
        <tbody>
          {runs?.map((run) => (
            <tr key={run.id} className="border-b hover:bg-slate-50">
              <td className="p-3">{run.vertical}</td>
              <td className="p-3">
                <span
                  className={
                    run.status === "completed"
                      ? "text-green-700"
                      : run.status === "escalated"
                      ? "text-amber-700"
                      : "text-slate-500"
                  }
                >
                  {run.status}
                </span>
              </td>
              <td className="p-3">{run.confidence?.toFixed(2) ?? "—"}</td>
              <td className="p-3">{new Date(run.created_at).toLocaleString()}</td>
              <td className="p-3">
                <Link to={`/runs/${run.id}`} className="text-blue-600 hover:underline">
                  View
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {runs?.length === 0 && <p className="text-slate-500 mt-4">No runs yet.</p>}
    </div>
  );
}

export default Dashboard;