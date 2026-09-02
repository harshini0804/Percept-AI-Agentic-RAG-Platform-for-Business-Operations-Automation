const API_BASE_URL = "http://127.0.0.1:8000";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null);
    throw new Error(errorBody?.detail || `Request failed: ${response.status}`);
  }

  return response.json();
}

// For multipart/form-data uploads (e.g. file uploads) — no
// Content-Type header is set here, since the browser needs to set
// its own multipart boundary automatically when the body is a
// FormData instance; setting it manually breaks the request.
async function apiFetchFormData<T>(path: string, formData: FormData): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null);
    throw new Error(errorBody?.detail || `Request failed: ${response.status}`);
  }

  return response.json();
}

export { apiFetch, apiFetchFormData };