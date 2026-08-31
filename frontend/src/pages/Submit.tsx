import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { submitRun } from "../api/agentRuns";

// Only "dummy" is registered on the backend right now. As real
// verticals get built and registered (Section 7, point 11), add
// their names here — this is the "parametrized per vertical"
// selector Section 5 describes; vertical-specific input fields
// (beyond a single text box) get added per vertical later, reusing
// this shell.
const AVAILABLE_VERTICALS = [
  { value: "dummy", label: "Dummy (test vertical)" },
];

function Submit() {
  const [vertical, setVertical] = useState(AVAILABLE_VERTICALS[0].value);
  const [inputText, setInputText] = useState("");
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => submitRun(vertical, inputText),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
      navigate(`/runs/${data.run_id}`);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    mutation.mutate();
  };

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

        <button
          type="submit"
          disabled={mutation.isPending || !inputText.trim()}
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