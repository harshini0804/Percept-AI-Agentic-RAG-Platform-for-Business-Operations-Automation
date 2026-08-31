import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listNotifications, markNotificationRead } from "../api/notifications";

function Notifications() {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const queryClient = useQueryClient();

  const { data: notifications, isLoading, error } = useQuery({
    queryKey: ["notifications", unreadOnly],
    queryFn: () => listNotifications(unreadOnly),
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
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(e) => setUnreadOnly(e.target.checked)}
          />
          Unread only
        </label>
      </div>

      {notifications?.length === 0 && (
        <p className="text-slate-500">No notifications to show.</p>
      )}

      <div className="flex flex-col gap-2">
        {notifications?.map((n) => (
          <div
            key={n.id}
            className={`bg-white rounded shadow p-4 flex justify-between items-start ${
              n.read ? "opacity-60" : ""
            }`}
          >
            <div>
              <p className="text-sm text-slate-500">{n.recipient}</p>
              <p className="text-sm">{n.message}</p>
              <div className="flex gap-2 items-center mt-1">
                <span className="text-xs text-slate-400">
                  {new Date(n.created_at).toLocaleString()}
                </span>
                <Link to={`/runs/${n.run_id}`} className="text-xs text-blue-600 hover:underline">
                  View run
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