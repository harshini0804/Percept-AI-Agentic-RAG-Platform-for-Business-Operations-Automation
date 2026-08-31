import { useQuery } from "@tanstack/react-query";
import { getEvaluationMetrics } from "../api/evaluation";

function pct(value: number): string {
  return `${(value * 100).toFixed(0)}%`;
}

function Evaluation() {
  const { data: metrics, isLoading, error } = useQuery({
    queryKey: ["evaluation-metrics"],
    queryFn: getEvaluationMetrics,
  });

  if (isLoading) return <p>Loading metrics...</p>;
  if (error) return <p className="text-red-600">Error: {(error as Error).message}</p>;

  if (!metrics || metrics.length === 0) {
    return (
      <div>
        <h2 className="text-xl font-semibold mb-4">Evaluation Dashboard</h2>
        <p className="text-slate-500">No runs yet — metrics will appear once verticals start processing cases.</p>
      </div>
    );
  }

  const totalRuns = metrics.reduce((sum, m) => sum + m.total_runs, 0);
  const totalCompleted = metrics.reduce((sum, m) => sum + m.completed_count, 0);
  const totalEscalated = metrics.reduce((sum, m) => sum + m.escalated_count, 0);

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Evaluation Dashboard</h2>
      <p className="text-sm text-slate-500 mb-6">
        Comparing how much of the shared architecture generalized across verticals (Section 11.2).
      </p>

      {/* Vital metric cards */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Total Runs</p>
          <p className="text-2xl font-semibold mt-1">{totalRuns}</p>
        </div>
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Completed</p>
          <p className="text-2xl font-semibold mt-1 text-green-700">{totalCompleted}</p>
        </div>
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Escalated</p>
          <p className="text-2xl font-semibold mt-1 text-amber-700">{totalEscalated}</p>
        </div>
        <div className="bg-white rounded shadow p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Verticals Active</p>
          <p className="text-2xl font-semibold mt-1">{metrics.length}</p>
        </div>
      </div>

      {/* Detailed table per vertical */}
      <div className="bg-white rounded shadow p-6 overflow-x-auto">
        <h3 className="font-medium mb-4">Detailed Metrics by Vertical</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b text-slate-500">
              <th className="py-2 pr-4">Vertical</th>
              <th className="py-2 pr-4">Total Runs</th>
              <th className="py-2 pr-4">Resolution Rate</th>
              <th className="py-2 pr-4">Escalation Rate</th>
              <th className="py-2 pr-4">Avg Confidence</th>
              <th className="py-2 pr-4">Confidence Range</th>
              <th className="py-2 pr-4">Retry Rate</th>
              <th className="py-2 pr-4">Tool Calls / Run</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => (
              <tr key={m.vertical} className="border-b">
                <td className="py-2 pr-4 font-medium">{m.vertical}</td>
                <td className="py-2 pr-4">{m.total_runs}</td>
                <td className="py-2 pr-4">{pct(m.resolution_rate)}</td>
                <td className="py-2 pr-4">{pct(m.escalation_rate)}</td>
                <td className="py-2 pr-4">
                  {m.avg_confidence !== null ? m.avg_confidence.toFixed(2) : "—"}
                </td>
                <td className="py-2 pr-4">
                  {m.min_confidence !== null && m.max_confidence !== null
                    ? `${m.min_confidence.toFixed(2)} – ${m.max_confidence.toFixed(2)}`
                    : "—"}
                </td>
                <td className="py-2 pr-4">{pct(m.retrieval_retry_rate)}</td>
                <td className="py-2 pr-4">{m.tool_calls_per_run.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default Evaluation;