import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { submitRun, submitRunWithFile } from "../api/agentRuns";

// Only "dummy" is registered on the backend right now. As real
// verticals get built and registered (Section 7, point 11), add
// their names here — this is the "parametrized per vertical"
// selector Section 5 describes; vertical-specific input fields
// (beyond text/file) get added per vertical later, reusing this
// shell.
const AVAILABLE_VERTICALS = [
  { value: "dummy", label: "Dummy (test vertical)" },
];

type SubmissionMode = "text" | "file";

function Submit() {
  const [vertical, setVertical] = useState(AVAILABLE_VERTICALS[0].value);
  const [mode, setMode] = useState<SubmissionMode>("text");
  const [inputText, setInputText] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      mode === "file" && selectedFile
        ? submitRunWithFile(vertical, selectedFile)
        : submitRun(vertical, inputText),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
      navigate(`/runs/${data.run_id}`);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === "text" && !inputText.trim()) return;
    if (mode === "file" && !selectedFile) return;
    mutation.mutate();
  };

  const canSubmit =
    !mutation.isPending &&
    ((mode === "text" && inputText.trim().length > 0) ||
      (mode === "file" && selectedFile !== null));

  return (
    <div className="max-w-xl">
      <h2 className="text-xl font-semibold mb-4">Submit for Analysis</h2>
      <form onSubmit={handleSubmit} className="bg-white rounded shadow p-6 flex flex-col gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">Vertical</label>
          <select
            value={vertical}
            onChange={(e) => setVertical(e.target.value)}
            className="w-full border rounded px-3 py-2"
          >
            {AVAILABLE_VERTICALS.map((v) => (
              <option key={v.value} value={v.value}>
                {v.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex gap-4 text-sm">
          <label className="flex items-center gap-1">
            <input
              type="radio"
              checked={mode === "text"}
              onChange={() => setMode("text")}
            />
            Paste text
          </label>
          <label className="flex items-center gap-1">
            <input
              type="radio"
              checked={mode === "file"}
              onChange={() => setMode("file")}
            />
            Upload file
          </label>
        </div>

        {mode === "text" ? (
          <div>
            <label className="block text-sm font-medium mb-1">Input</label>
            <textarea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              rows={6}
              className="w-full border rounded px-3 py-2"
              placeholder="Describe the incident, contract clause, meeting note, etc."
            />
          </div>
        ) : (
          <div>
            <label className="block text-sm font-medium mb-1">File</label>
            <input
              type="file"
              accept=".txt,.pdf,.docx"
              onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
              className="w-full border rounded px-3 py-2 bg-white"
            />
            <p className="text-xs text-slate-500 mt-1">Accepted: .txt, .pdf, .docx</p>
          </div>
        )}

        <button
          type="submit"
          disabled={!canSubmit}
          className="bg-slate-900 text-white rounded px-4 py-2 disabled:opacity-50"
        >
          {mutation.isPending ? "Analyzing..." : "Analyze"}
        </button>

        {mutation.isError && (
          <p className="text-red-600 text-sm">{(mutation.error as Error).message}</p>
        )}
      </form>
    </div>
  );
}

export default Submit;