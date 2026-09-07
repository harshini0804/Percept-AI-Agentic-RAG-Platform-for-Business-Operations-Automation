import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import { listNotifications, markNotificationRead } from "../api/notifications";

// Mirrors the four real verticals (Section 4.3) plus the dummy
// reference vertical — same constant used in Escalations.tsx, kept
// here too since the backend doesn't expose a canonical vertical
// list endpoint.
const VERTICAL_OPTIONS = [
  { value: "", label: "All verticals" },
  { value: "dummy", label: "Dummy" },
  { value: "post_incident", label: "Post-Incident" },
  { value: "internal_mobility", label: "Internal Mobility" },
  { value: "contract_tracking", label: "Contract Tracking" },
  { value: "meeting_action_items", label: "Meeting Action Items" },
];

function Notifications() {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [vertical, setVertical] = useState("");
  const queryClient = useQueryClient();

  const { data: notifications, isLoading, error } = useQuery({
    queryKey: ["notifications", unreadOnly, vertical],
    queryFn: () => listNotifications(unreadOnly, vertical || undefined),
  });

  const mutation = useMutation({
    mutationFn: (id: string) => markNotificationRead(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  if (isLoading) return <p>Loading notifications...</p>;
  if (error) return <p className="text-red-600">Error: {(error as Error).message}</p>;

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">Notifications</h2>
        <div className="flex items-center gap-4">
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
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={unreadOnly}
              onChange={(e) => setUnreadOnly(e.target.checked)}
            />
            Unread only
          </label>
        </div>
      </div>

      {notifications?.length === 0 && (
        <p className="text-slate-500">No notifications to show.</p>
      )}

      <div className="flex flex-col gap-2">
        {notifications?.map((n) => (
          <div
            key={n.id}
            className={`bg-white rounded-xl shadow-md border border-slate-200 p-4 flex justify-between items-start ${
              n.read ? "opacity-60" : ""
            }`}
          >
            <div>
              <span className="text-xs uppercase tracking-wide text-slate-400">
                {n.vertical}
              </span>
              <p className="text-sm text-slate-500 mt-0.5">{n.recipient}</p>
              <p className="text-sm">{n.message}</p>
              <div className="flex gap-2 items-center mt-1">
                <span className="text-xs text-slate-400">
                  {new Date(n.created_at).toLocaleString()}
                </span>
                <Link
                  to={`/runs/${n.run_id}`}
                  title="View run"
                  className="inline-flex text-slate-400 hover:text-blue-600 hover:bg-slate-100 rounded-full p-1 transition-colors"
                >
                  <ExternalLink size={14} />
                </Link>
              </div>
            </div>
            {!n.read && (
              <button
                onClick={() => mutation.mutate(n.id)}
                disabled={mutation.isPending}
                className="text-xs bg-slate-100 hover:bg-slate-200 px-2 py-1 rounded disabled:opacity-50"
              >
                Mark read
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default Notifications;