import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listAgentRuns, getAgentRunStats } from "../api/agentRuns";
import { ExternalLink } from "lucide-react";

// Mirrors the four real verticals (Section 4.3) plus the dummy
// reference vertical — same constant used in Escalations.tsx and
// Notifications.tsx, kept here too since the backend doesn't expose
// a canonical vertical list endpoint.
const VERTICAL_OPTIONS = [
  { value: "", label: "All verticals" },
  { value: "dummy", label: "Dummy" },
  { value: "post_incident", label: "Post-Incident" },
  { value: "internal_mobility", label: "Internal Mobility" },
  { value: "contract_tracking", label: "Contract Tracking" },
  { value: "meeting_action_items", label: "Meeting Action Items" },
];

function Dashboard() {
  const [vertical, setVertical] = useState("");

  const { data: runs, isLoading: runsLoading, error: runsError } = useQuery({
    queryKey: ["agent-runs", vertical],
    queryFn: () => listAgentRuns(vertical || undefined),
  });

  // Fetched separately from the recent-runs list — the list is
  // capped at 50 rows for display, but the summary cards need a
  // real aggregate count across ALL matching runs, not just one page.
  const { data: stats, isLoading: statsLoading, error: statsError } = useQuery({
    queryKey: ["agent-run-stats", vertical],
    queryFn: () => getAgentRunStats(vertical || undefined),
  });

  if (runsLoading || statsLoading) return <p>Loading runs...</p>;
  if (runsError) return <p className="text-red-600">Error: {(runsError as Error).message}</p>;
  if (statsError) return <p className="text-red-600">Error: {(statsError as Error).message}</p>;

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">Dashboard</h2>
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

      {/* Vital metric cards — real aggregate counts, scoped to the
          selected vertical (or all, if none selected), not derived
          from the (possibly truncated) recent-runs list below. */}
      <div className="grid grid-cols-5 gap-4 mb-6">
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Total Runs</p>
          <p className="text-2xl font-semibold mt-1">{stats?.total ?? 0}</p>
        </div>
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Completed</p>
          <p className="text-2xl font-semibold mt-1 text-green-700">{stats?.completed ?? 0}</p>
        </div>
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Escalated</p>
          <p className="text-2xl font-semibold mt-1 text-amber-700">{stats?.escalated ?? 0}</p>
        </div>
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Rejected</p>
          <p className="text-2xl font-semibold mt-1 text-red-700">{stats?.rejected ?? 0}</p>
        </div>
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">In Progress</p>
          <p className="text-2xl font-semibold mt-1 text-slate-500">{stats?.running ?? 0}</p>
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
                      : run.status === "rejected"
                      ? "text-red-700"
                      : "text-slate-500"
                  }
                >
                  {run.status}
                </span>
              </td>
              <td className="p-3">{run.confidence?.toFixed(2) ?? "—"}</td>
              <td className="p-3">{new Date(run.created_at).toLocaleString()}</td>
              <td className="p-3">
                <Link
                  to={`/runs/${run.id}`}
                  title="View run"
                  className="inline-flex text-slate-400 hover:text-blue-600 hover:bg-slate-100 rounded-full p-1.5 transition-colors"
                >
                  <ExternalLink size={16} />
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