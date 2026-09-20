// Shared score formatting — replaces inconsistent raw-decimal vs.
// percentage display, and turns a bare retrieval score/dash into a
// qualitative, understandable label.

export function formatConfidence(confidence: number | null | undefined): string {
  if (confidence === null || confidence === undefined) return "—";
  return `${Math.round(confidence * 100)}%`;
}

export function describeRetrievalScore(
  score: number | null | undefined,
  numResults: number | null | undefined
): string {
  if (!numResults || numResults === 0 || score === null || score === undefined) {
    return "No similar cases found in the knowledge base";
  }

  const resultWord = numResults === 1 ? "result" : "results";
  const scoreLabel = formatConfidence(score);

  if (score >= 0.75) {
    return `Strong match — ${scoreLabel} similarity (${numResults} ${resultWord})`;
  }
  if (score >= 0.4) {
    return `Possible match — ${scoreLabel} similarity (${numResults} ${resultWord})`;
  }
  return `Weak match — ${scoreLabel} similarity (${numResults} ${resultWord})`;
}