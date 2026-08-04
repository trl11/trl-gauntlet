import clsx from "clsx";

import type { Verdict } from "@api/types";

import "./VerdictBanner.scss";

/** Props for {@link VerdictBanner}. */
export interface VerdictBannerProps {
  /** `verdict.json`, or the partial summary the verdict event carries. */
  verdict: Partial<Verdict> | null;
}

/** The run's PASSED/FAILED outcome, shown above the tabs so every tab carries it. */
export const VerdictBanner: React.FC<VerdictBannerProps> = ({ verdict }) => {
  if (verdict === null) return null;

  const passed = verdict.passed === true;

  return (
    <section
      className={clsx(
        "verdict-banner",
        passed ? "verdict-banner--passed" : "verdict-banner--failed"
      )}
      aria-label="Verdict"
    >
      <span className="verdict-banner__result">{passed ? "PASSED" : "FAILED"}</span>
      {verdict.reason && <span className="verdict-banner__reason">{verdict.reason}</span>}
    </section>
  );
};

export default VerdictBanner;
