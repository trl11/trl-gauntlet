/** How healthy a pass rate (0-100) looks, as a class the stylesheet colours. */
export function health(rate: number): "is-good" | "is-fair" | "is-poor" {
  if (rate >= 90) return "is-good";
  if (rate >= 70) return "is-fair";
  return "is-poor";
}
