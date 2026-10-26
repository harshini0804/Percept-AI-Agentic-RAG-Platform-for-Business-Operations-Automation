import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import { listIntegrationEvents } from "../api/integrations";
import { capitalizeWords } from "../utils/formatDecision";

const SOURCE_OPTIONS = [
  { value: "", label: "All sources" },
  { value: "zoom", label: "Zoom" },
  { value: "github", label: "GitHub" },
];

const SOURCE_STYLES: Record<string, { label: string; badge: string }> = {
  zoom: { label: "Zoom", badge: "bg-blue-100 text-blue-700" },
  github: { label: "GitHub", badge: "bg-slate-200 text-slate-700" },
};

// What each status means, in plain words. The backend's status values are
// internal; this is the wording a reviewer should see.
const STATUS_STYLES: Record<string, { label: string; badge: string }> = {
  received: { label: "Processing…", badge: "bg-amber-100 text-amber-700" },
  processed: { label: "Handled", badge: "bg-green-100 text-green-700" },
  needs_review: { label: "Sent for review", badge: "bg-orange-100 text-orange-700" },
  ignored: { label: "Ignored", badge: "bg-slate-100 text-slate-600" },
  duplicate: { label: "Repeat — not processed again", badge: "bg-slate-100 text-slate-600" },
  rejected: { label: "Rejected", badge: "bg-red-100 text-red-700" },
  failed: { label: "Failed", badge: "bg-red-100 text-red-700" },
};

function sourceStyle(source: string) {
  return SOURCE_STYLES[source] ?? { label: capitalizeWords(source), badge: "bg-slate-100 text-slate-600" };
}

function statusStyle(status: string) {
  return STATUS_STYLES[status] ?? { label: status, badge: "bg-slate-100 text-slate-600" };
}

function LiveFeed() {
  const [source, setSource] = useState("");

  // Refreshes by itself so messages appear as they arrive during a demo.
  const { data: events, isLoading, error } = useQuery({
    queryKey: ["integration-events", source],
    queryFn: () => listIntegrationEvents(source || undefined),
    refetchInterval: 3000,
  });

  if (isLoading) return <p>Loading live feed...</p>;
  if (error) {
    return (
      <p className="text-red-600">
        Something went wrong loading the live feed. ({(error as Error).message})
      </p>
    );
  }

  const list = events ?? [];
  const count = (status: string) => list.filter((e) => e.status === status).length;

  return (
    <div>
      <div className="flex justify-between items-start mb-4">
        <div>
          <h2 className="text-xl font-semibold">Live Feed</h2>
          <p className="text-sm text-slate-500 mt-1">
            Messages the platform receives automatically from Zoom and GitHub — no files uploaded.
            Every message is checked against a shared secret before anything in it is read.
          </p>
        </div>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white"
        >
          {SOURCE_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-4 gap-3 mb-4">
        {[
          { label: "Received", value: list.length, color: "text-slate-800" },
          { label: "Handled", value: count("processed") + count("needs_review"), color: "text-green-700" },
          { label: "Rejected", value: count("rejected"), color: "text-red-700" },
          { label: "Repeats / ignored", value: count("duplicate") + count("ignored"), color: "text-slate-500" },
        ].map((card) => (
          <div key={card.label} className="bg-white rounded-xl shadow-md border border-slate-200 px-4 py-3">
            <p className="text-xs text-slate-500">{card.label}</p>
            <p className={`text-2xl font-semibold ${card.color}`}>{card.value}</p>
          </div>
        ))}
      </div>

      {list.length === 0 && (
        <p className="text-slate-500">
          Nothing received yet. Messages from Zoom and GitHub will appear here as they arrive.
        </p>
      )}

      <div className="flex flex-col gap-3">
        {list.map((event) => {
          const src = sourceStyle(event.source);
          const st = statusStyle(event.status);
          return (
            <div
              key={event.id}
              className={`bg-white rounded-xl shadow-md border p-4 ${
                event.status === "rejected" || event.status === "failed"
                  ? "border-red-300"
                  : "border-slate-200"
              }`}
            >
              <div className="flex justify-between items-start gap-4">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${src.badge}`}>{src.label}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${st.badge}`}>{st.label}</span>
                    {event.owner && (
                      <span className="text-sm font-medium">{capitalizeWords(event.owner)}</span>
                    )}
                  </div>
                  <p className="text-sm text-slate-700">{event.summary ?? "No details recorded."}</p>
                  <p className="text-xs text-slate-400 mt-1.5">
                    {new Date(event.received_at).toLocaleTimeString()} ·{" "}
                    {new Date(event.received_at).toLocaleDateString()}
                  </p>
                </div>
                {event.run_id && (
                  <Link
                    to={`/runs/${event.run_id}`}
                    className="inline-flex text-slate-400 hover:text-blue-600 hover:bg-slate-100 rounded-full p-1 transition-colors"
                    title="View the run this message caused"
                  >
                    <ExternalLink size={14} />
                  </Link>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default LiveFeed;
