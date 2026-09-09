import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listTrackerItems, resolveActionItem } from "../api/meetingActionItems";

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
];

function formatDeadline(deadline: string | null): string {
  if (!deadline) return "No deadline";
  const isOverdue = new Date(deadline) < new Date(new Date().toDateString());
  return isOverdue ? `${deadline} (overdue)` : deadline;
}

function Tracker() {
  const [status, setStatus] = useState("");
  const queryClient = useQueryClient();

  const { data: items, isLoading, error } = useQuery({
    queryKey: ["action-item-tracker", status],
    queryFn: () => listTrackerItems(status || undefined),
  });

  const mutation = useMutation({
    mutationFn: (id: string) => resolveActionItem(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["action-item-tracker"] });
    },
  });

  if (isLoading) return <p>Loading tracker...</p>;
  if (error) return <p className="text-red-600">Error: {(error as Error).message}</p>;

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <div>
          <h2 className="text-xl font-semibold">Action Item Tracker</h2>
          <p className="text-sm text-slate-500 mt-1">
            Meeting Action Items — a living status view, not a one-time report.
          </p>
        </div>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      {items?.length === 0 && <p className="text-slate-500">No action items to show.</p>}

      <div className="flex flex-col gap-3">
        {items?.map((item) => (
          <div
            key={item.id}
            className={`bg-white rounded-xl shadow-md border p-4 ${
              item.escalated && item.status === "open"
                ? "border-red-300"
                : "border-slate-200"
            } ${item.status === "resolved" ? "opacity-60" : ""}`}
          >
            <div className="flex justify-between items-start">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-medium">{item.owner}</span>
                  {item.is_recurring && (
                    <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">
                      Recurring
                    </span>
                  )}
                  {item.escalated && (
                    <span className="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-full">
                      Escalated
                    </span>
                  )}
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full ${
                      item.status === "resolved"
                        ? "bg-green-100 text-green-700"
                        : "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {item.status}
                  </span>
                </div>
                <p className="text-sm text-slate-700">{item.description}</p>
                <div className="flex items-center gap-3 mt-1.5 text-xs text-slate-400">
                  <span>Deadline: {formatDeadline(item.deadline)}</span>
                  <span>·</span>
                  <span>Nudges: {item.nudge_count}</span>
                  <span>·</span>
                  <span>Created {new Date(item.created_at).toLocaleDateString()}</span>
                </div>
              </div>

              {item.status !== "resolved" && (
                <button
                  onClick={() => mutation.mutate(item.id)}
                  disabled={mutation.isPending}
                  title="Manually mark this item resolved — for cases resolved outside the tracked system (e.g. a direct conversation with the manager)"
                  className="text-xs bg-green-600 text-white px-3 py-1.5 rounded-lg disabled:opacity-50 whitespace-nowrap"
                >
                  Mark Resolved
                </button>
              )}
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

export default Tracker;