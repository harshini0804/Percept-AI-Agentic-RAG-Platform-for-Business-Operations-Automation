import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Paperclip, X, ArrowUp, FileText } from "lucide-react";
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

function Submit() {
  const [vertical, setVertical] = useState(AVAILABLE_VERTICALS[0].value);
  const [inputText, setInputText] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      selectedFile
        ? submitRunWithFile(vertical, selectedFile)
        : submitRun(vertical, inputText),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
      navigate(`/runs/${data.run_id}`);
    },
  });

  const canSubmit =
    !mutation.isPending && (inputText.trim().length > 0 || selectedFile !== null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    mutation.mutate();
  };

  const handleAttachClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    setSelectedFile(file);
    // The backend accepts either pasted text OR a file, never both
    // (AgentRunInput requires exactly one of input_document_id /
    // input_payload) — attaching a file clears any typed text so the
    // UI never implies both would be sent.
    if (file) setInputText("");
  };

  const removeFile = () => {
    setSelectedFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <div className="max-w-2xl">
      <h2 className="text-xl font-semibold mb-4">Submit for Analysis</h2>

      <div className="mb-4">
        <label className="block text-sm font-medium mb-1 text-slate-600">Vertical</label>
        <select
          value={vertical}
          onChange={(e) => setVertical(e.target.value)}
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white"
        >
          {AVAILABLE_VERTICALS.map((v) => (
            <option key={v.value} value={v.value}>
              {v.label}
            </option>
          ))}
        </select>
      </div>

      <form onSubmit={handleSubmit}>
        <div className="bg-white rounded-2xl shadow-md border border-slate-200 p-3">
          {selectedFile && (
            <div className="flex items-center gap-2 bg-slate-100 rounded-lg px-3 py-2 mb-2 w-fit">
              <FileText size={16} className="text-slate-500" />
              <span className="text-sm text-slate-700">{selectedFile.name}</span>
              <button
                type="button"
                onClick={removeFile}
                className="text-slate-400 hover:text-slate-700"
                aria-label="Remove attached file"
              >
                <X size={14} />
              </button>
            </div>
          )}

          <textarea
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            disabled={!!selectedFile}
            rows={4}
            className="w-full resize-none border-none focus:outline-none text-sm disabled:bg-transparent disabled:text-slate-400"
            placeholder={
              selectedFile
                ? "Remove the attached file to type text instead."
                : "Describe the incident, contract clause, meeting note, etc."
            }
          />

          <div className="flex justify-between items-center mt-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.pdf,.docx"
              onChange={handleFileChange}
              className="hidden"
            />
            <button
              type="button"
              onClick={handleAttachClick}
              disabled={inputText.trim().length > 0}
              title="Attach a file (.txt, .pdf, .docx)"
              className="text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded-full p-2 disabled:opacity-40 disabled:hover:bg-transparent transition-colors"
            >
              <Paperclip size={18} />
            </button>

            <button
              type="submit"
              disabled={!canSubmit}
              title="Analyze"
              className="bg-slate-900 text-white rounded-full p-2 disabled:opacity-40 hover:bg-slate-700 transition-colors"
            >
              <ArrowUp size={18} />
            </button>
          </div>
        </div>

        {mutation.isPending && (
          <p className="text-sm text-slate-500 mt-2">Analyzing...</p>
        )}
        {mutation.isError && (
          <p className="text-red-600 text-sm mt-2">{(mutation.error as Error).message}</p>
        )}
      </form>
    </div>
  );
}

export default Submit;