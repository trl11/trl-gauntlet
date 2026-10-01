import { useState } from "react";

/** How the lines of a graph share the page: each on a graph of its own, or all on one. */
export type Layout = "separate" | "stacked";

/** Most lines worth giving a graph each before one graph is the better start. */
const MOST_SEPARATE = 4;

/**
 * The layout of a set of lines, and the way to change it.
 *
 * It starts as one graph each while there are few enough for that to fit, and
 * as one graph for all of them otherwise. Once the operator chooses, their
 * choice stands however many lines there are.
 */
export function useLayout(lines: number): [Layout, (choice: Layout) => void] {
  const [chosen, setChosen] = useState<Layout | null>(null);
  return [chosen ?? (lines <= MOST_SEPARATE ? "separate" : "stacked"), setChosen];
}

export default useLayout;
