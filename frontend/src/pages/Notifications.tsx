import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ExternalLink, Check } from "lucide-react";
import { listNotifications, markNotificationRead } from "../api/notifications";
import { getVerticalLabel, VERTICAL_LABELS } from "../utils/verticals";

const VERTICAL_OPTIONS = [
  { value: "", label: "All verticals" },
  ...Object.entries(VERTICAL_LABELS).map(([value, label]) => ({ value, label })),
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
  if (error) return <p className="text-red-600">Something went wrong loading notifications. ({(error as Error).message})</p>;

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
                {getVerticalLabel(n.vertical)}
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
                title="Mark as read"
                className="inline-flex items-center justify-center w-7 h-7 rounded-full text-slate-400 hover:text-green-700 hover:bg-green-50 cursor-pointer transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Check size={16} />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default Notifications;