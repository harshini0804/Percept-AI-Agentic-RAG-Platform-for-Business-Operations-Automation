import { apiFetch } from "./client";

export interface NotificationSummary {
  id: string;
  run_id: string;
  vertical: string;
  recipient: string;
  message: string;
  read: boolean;
  created_at: string;
}

export function listNotifications(
  unreadOnly?: boolean,
  vertical?: string
): Promise<NotificationSummary[]> {
  const params = new URLSearchParams();
  if (unreadOnly) params.set("unread_only", "true");
  if (vertical) params.set("vertical", vertical);
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiFetch(`/notifications${query}`);
}

export function markNotificationRead(notificationId: string): Promise<NotificationSummary> {
  return apiFetch(`/notifications/${notificationId}/mark-read`, {
    method: "POST",
  });
}