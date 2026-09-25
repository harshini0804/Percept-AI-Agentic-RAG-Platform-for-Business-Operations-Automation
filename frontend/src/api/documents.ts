import { apiFetch } from "./client";

export interface DocumentContent {
  id: string;
  filename: string;
  vertical: string;
  uploaded_at: string;
  content: string;
}

export function getDocument(documentId: string): Promise<DocumentContent> {
  return apiFetch(`/documents/${documentId}`);
}