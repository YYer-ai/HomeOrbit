export function computeCompositeScore(
  scores: Record<string, unknown>,
  weights: Record<string, unknown>,
): number | null {
  const enabled: Array<{ score: number; weight: number }> = [];
  let maxWeight = 0;

  for (const [category, weight] of Object.entries(weights)) {
    if (typeof weight !== "number" || !Number.isFinite(weight) || weight < 0) {
      return null;
    }
    if (weight === 0) continue;

    const score = scores[category];
    if (
      typeof score !== "number" ||
      !Number.isFinite(score) ||
      score < 0 ||
      score > 100
    ) {
      return null;
    }
    enabled.push({ score, weight });
    maxWeight = Math.max(maxWeight, weight);
  }

  if (maxWeight === 0) return null;

  let weightedTotal = 0;
  let totalWeight = 0;
  for (const { score, weight } of enabled) {
    const normalizedWeight = weight / maxWeight;
    weightedTotal += score * normalizedWeight;
    totalWeight += normalizedWeight;
  }

  const result = weightedTotal / totalWeight;
  return Number.isFinite(result) && result >= 0 && result <= 100 ? result : null;
}
