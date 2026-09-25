import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { formatConfidence } from "../utils/formatScore";

export interface RetrievedResult {
  id: string;
  chunk_text: string;
  similarity: number;
}

// Renders the actual retrieved KB chunks as a clickable list — each
// item collapsed to a short preview + score badge by default,
// expanding to show the full chunk text on click. Only rendered when
// a retrieval decision's detail carries a real "results" array (UI
// feature 3) — verticals whose retrieval logging hasn't been
// extended to include it (as of this writing, only contract_tracking,
// which uses its own custom clause-retrieval logging rather than the
// shared retrieve_node) simply don't show this list; the plain
// summary line above it still works for them exactly as before.
function ResultItem({ result }: { result: RetrievedResult }) {
  const [expanded, setExpanded] = useState(false);
  const preview =
    result.chunk_text.length > 60 ? `${result.chunk_text.slice(0, 60)}…` : result.chunk_text;

  return (
    <div className="border border-slate-200 rounded-lg">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex items-center justify-between w-full text-left px-3 py-2 cursor-pointer hover:bg-slate-50 transition-colors"
      >
        <span className="text-sm text-slate-700 truncate mr-3">{preview}</span>
        <span className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-slate-500">{formatConfidence(result.similarity)}</span>
          {expanded ? (
            <ChevronUp size={14} className="text-slate-400" />
          ) : (
            <ChevronDown size={14} className="text-slate-400" />
          )}
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-3">
          <p className="text-sm text-slate-700 whitespace-pre-wrap bg-slate-50 p-2 rounded">
            {result.chunk_text}
          </p>
        </div>
      )}
    </div>
  );
}

export function RetrievedResultsList({ results }: { results: RetrievedResult[] }) {
  if (results.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 mt-3">
      {results.map((r) => (
        <ResultItem key={r.id} result={r} />
      ))}
    </div>
  );
}