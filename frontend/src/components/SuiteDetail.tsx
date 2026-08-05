import { faCircleXmark } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Badge, Button } from "@trl11/components/ui";
import clsx from "clsx";

import type { Suite, VerifyReport } from "@api/types";

import "./SuiteDetail.scss";

/** Props for {@link SuiteDetail}. */
export interface SuiteDetailProps {
  onEditProfile: (name: string) => void;
  onSelectProfile: (name: string) => void;
  onStart: () => void;
  selectedProfile: string | null;
  suite: Suite;
  /** Requirements of this suite that no available instrument satisfies. */
  unmet: string[];
  /** This suite's report from the last rescan, or null if it has not been verified. */
  verify: VerifyReport | null;
}

/** Everything known about one suite, and the actions it offers. */
const SuiteDetail: React.FC<SuiteDetailProps> = ({
  onEditProfile,
  onSelectProfile,
  onStart,
  selectedProfile,
  suite,
  unmet,
  verify,
}) => {
  const profiles = suite.profiles_available ?? [];
  const failures = verify?.checks.filter((check) => !check.passed) ?? [];

  return (
    <section className="suite-detail__detail" aria-label={suite.title}>
      <header className="suite-detail__detail-head">
        <div>
          <h2 className="suite-detail__detail-title">{suite.title}</h2>
          <p className="suite-detail__detail-key mono">{suite.key}</p>
        </div>
        <div className="suite-detail__detail-actions">
          <Button color="blue" onClick={onStart} disabled={unmet.length > 0}>
            Start run
          </Button>
        </div>
      </header>

      {suite.description && <p className="suite-detail__description">{suite.description}</p>}

      {unmet.length > 0 && (
        <p className="suite-detail__blocked" role="status">
          Cannot start: {unmet.join(", ")} {unmet.length === 1 ? "is" : "are"} unavailable. Check
          the Instruments page.
        </p>
      )}

      {failures.length > 0 && (
        <div className="suite-detail__verify">
          <h3 className="suite-detail__section-title">
            {`Conformance failed: ${failures.length} of ${verify?.checks.length} checks`}
          </h3>
          <ul className="suite-detail__checks">
            {failures.map((check) => (
              <li key={check.name} className="suite-detail__check">
                <FontAwesomeIcon
                  icon={faCircleXmark}
                  className="suite-detail__bad"
                  aria-hidden="true"
                />
                <span className="mono">{check.name}</span>
                <span className="muted">{check.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="suite-detail__profiles">
        <h3 className="suite-detail__section-title">Profiles</h3>
        {profiles.length === 0 ? (
          <p className="muted">This suite ships no profiles.</p>
        ) : (
          <ul className="suite-detail__profile-list">
            {profiles.map((entry) => (
              <li key={entry.name} className="suite-detail__profile">
                <button
                  type="button"
                  className={clsx(
                    "suite-detail__profile-pick",
                    selectedProfile === entry.name && "suite-detail__profile-pick--active"
                  )}
                  aria-pressed={selectedProfile === entry.name}
                  onClick={() => onSelectProfile(entry.name)}
                >
                  <span className="mono">{entry.name}</span>
                  {entry.description && (
                    <span className="suite-detail__profile-description">{entry.description}</span>
                  )}
                </button>
                {entry.user_authored && <Badge color="amber">edited</Badge>}
                <Button size="small" onClick={() => onEditProfile(entry.name)}>
                  Edit
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
};

export default SuiteDetail;
