import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { resyncVertical, getSyncHistory, type ResyncResponse } from "../api/admin";

// Matches VERTICAL_SOURCE_TYPES in backend/app/api/admin.py — the
// four verticals with staging-folder-based ingestion (Section 8).
const VERTICALS = [
  "post_incident",
  "internal_mobility",
  "contract_tracking",
  "meeting_action_items",
];

function Admin() {
  const [lastResult, setLastResult] = useState<ResyncResponse | null>(null);
  const queryClient = useQueryClient();

  const { data: history, isLoading } = useQuery({
    queryKey: ["sync-history"],
    queryFn: () => getSyncHistory(),
  });

  const mutation = useMutation({
    mutationFn: (vertical: string) => resyncVertical(vertical),
    onSuccess: (data) => {
      setLastResult(data);
      queryClient.invalidateQueries({ queryKey: ["sync-history"] });
    },
  });

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Admin Panel</h2>

      <div className="bg-white rounded shadow p-6 mb-6">
        <h3 className="font-medium mb-3">Resync Knowledge Base</h3>
        <div className="flex gap-2 flex-wrap">
          {VERTICALS.map((v) => (
            <button
              key={v}
              onClick={() => mutation.mutate(v)}
              disabled={mutation.isPending}
              className="bg-slate-900 text-white text-sm px-3 py-2 rounded disabled:opacity-50"
            >
              Resync: {v}
            </button>
          ))}
        </div>

        {lastResult && (
          <div className="mt-4 text-sm bg-slate-50 rounded p-3">
            <p className="font-medium mb-1">{lastResult.vertical}</p>
            <p className="text-green-700">Processed: {lastResult.processed.length}</p>
            <p className="text-slate-500">Skipped (unchanged): {lastResult.skipped.length}</p>
            {lastResult.errors.length > 0 && (
              <p className="text-red-600">Errors: {lastResult.errors.join(", ")}</p>
            )}
          </div>
        )}

        {mutation.isError && (
          <p className="text-red-600 text-sm mt-2">{(mutation.error as Error).message}</p>
        )}
      </div>

      <div className="bg-white rounded shadow p-6">
        <h3 className="font-medium mb-3">Sync History</h3>
        {isLoading && <p className="text-sm text-slate-500">Loading...</p>}
        {history?.length === 0 && (
          <p className="text-sm text-slate-500">No files synced yet.</p>
        )}
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b text-slate-500">
              <th className="py-2">Vertical</th>
              <th className="py-2">File</th>
              <th className="py-2">Last Synced</th>
            </tr>
          </thead>
          <tbody>
            {history?.map((h, i) => (
              <tr key={i} className="border-b">
                <td className="py-2">{h.vertical}</td>
                <td className="py-2 font-mono text-xs">{h.file_path}</td>
                <td className="py-2">{new Date(h.last_synced_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default Admin;