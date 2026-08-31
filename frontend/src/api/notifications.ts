import { apiFetch } from "./client";

export interface NotificationSummary {
  id: string;
  run_id: string;
  recipient: string;
  message: string;
  read: boolean;
  created_at: string;
}

export function listNotifications(unreadOnly?: boolean): Promise<NotificationSummary[]> {
  const query = unreadOnly ? "?unread_only=true" : "";
  return apiFetch(`/notifications${query}`);
}

export function markNotificationRead(notificationId: string): Promise<NotificationSummary> {
  return apiFetch(`/notifications/${notificationId}/mark-read`, {
    method: "POST",
  });
}