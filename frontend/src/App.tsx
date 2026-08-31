import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import Dashboard from "./pages/Dashboard.tsx";
import Submit from "./pages/Submit";
import RunDetail from "./pages/RunDetail";
import Escalations from "./pages/Escalations";


const NAV_ITEMS = [
  { path: "/", label: "Dashboard" },
  { path: "/submit", label: "Submit" },
  { path: "/escalations", label: "Escalations" },
  { path: "/notifications", label: "Notifications" },
  { path: "/admin", label: "Admin" },
  { path: "/evaluation", label: "Evaluation" },
];

function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen">
        <nav className="w-56 bg-slate-900 text-slate-100 p-4 flex flex-col gap-2">
          <h1 className="text-lg font-semibold mb-4">Agentic RAG Platform</h1>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                `px-3 py-2 rounded ${isActive ? "bg-slate-700" : "hover:bg-slate-800"}`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <main className="flex-1 overflow-y-auto p-6 bg-slate-50">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/submit" element={<Submit />} />
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route path="/escalations" element={<Escalations />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;