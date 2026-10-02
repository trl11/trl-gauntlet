import { useState } from "react";

/** What the operator has hidden, and the way to hide or show a line. */
export function useHidden(): [string[], (name: string) => void] {
  const [hidden, setHidden] = useState<string[]>([]);
  const toggle = (name: string) =>
    setHidden((current) =>
      current.includes(name) ? current.filter((entry) => entry !== name) : [...current, name]
    );
  return [hidden, toggle];
}

export default useHidden;
