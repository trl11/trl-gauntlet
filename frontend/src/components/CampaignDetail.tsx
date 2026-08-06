import { faPlay } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Spinner } from "@trl11/components/ui";
import clsx from "clsx";
import { useNavigate } from "react-router";

import { getCampaign, runCampaignMember } from "@api/client";
import type { CampaignMember } from "@api/types";
import EmptyState from "@components/EmptyState";

import "./CampaignDetail.scss";

/**
 * Which optional columns any member of this campaign fills.
 *
 * A campaign declares only what its test plan cares about, so a column no
 * member populates is left out rather than drawn as a row of dashes.
 */
function columns(members: CampaignMember[]): { component: boolean; fixture: boolean } {
  return {
    component: members.some((member) => member.component !== ""),
    fixture: members.some((member) => member.fixture !== ""),
  };
}

/** Props for {@link MemberRow}. */
interface MemberRowProps {
  busy: boolean;
  member: CampaignMember;
  onRun: (suite: string) => void;
  show: { component: boolean; fixture: boolean };
}

/** One member of a campaign. */
const MemberRow: React.FC<MemberRowProps> = ({ busy, member, onRun, show }) => (
  <tr className={clsx(!member.present && "campaign-detail__row--absent")}>
    <th scope="row">
      {/* The cell stays a table cell so it sizes with the column; the flex row
          is inside it. */}
      <span className="campaign-detail__member">
        <span className="campaign-detail__member-title">{member.title || member.suite}</span>
        <span className="campaign-detail__member-key mono">{member.suite}</span>
        {!member.declared && (
          <span className="campaign-detail__tag" title="Found in the campaign directory">
            undeclared
          </span>
        )}
        {!member.present && (
          <span className="campaign-detail__tag campaign-detail__tag--absent">not on disk</span>
        )}
      </span>
    </th>
    {show.component && <td className="mono">{member.component || "—"}</td>}
    {show.fixture && <td className="mono">{member.fixture || "—"}</td>}
    <td className="mono">{member.profile || "—"}</td>
    <td>
      <Button size="small" disabled={!member.present || busy} onClick={() => onRun(member.suite)}>
        <FontAwesomeIcon icon={faPlay} aria-hidden="true" /> Run
      </Button>
    </td>
  </tr>
);

/** Props for {@link CampaignDetail}. */
export interface CampaignDetailProps {
  /** Key of the campaign to show. */
  campaignKey: string;
}

/**
 * One campaign: what it groups, and what each of its suites has done.
 *
 * A campaign does not sequence its members. Running one starts a single run
 * with the profile and overrides the campaign declares for that suite, so
 * re-running after editing the manifest is the same button again.
 */
export const CampaignDetail: React.FC<CampaignDetailProps> = ({ campaignKey }) => {
  const navigate = useNavigate();

  const campaign = useQuery({
    queryKey: ["campaign", campaignKey],
    queryFn: () => getCampaign(campaignKey),
  });

  const start = useMutation({
    mutationFn: (suite: string) => runCampaignMember(campaignKey, suite),
    onSuccess: (run) => navigate(`/runs/${encodeURIComponent(run.run_id)}`),
  });

  const members = campaign.data?.members ?? [];
  const show = columns(members);

  return (
    <section className="campaign-detail" aria-label={campaign.data?.title ?? campaignKey}>
      {campaign.isLoading && <Spinner className="campaign-detail__spinner" />}
      {campaign.isError && (
        <p className="campaign-detail__blocked" role="alert">
          {campaign.error.message}
        </p>
      )}

      {campaign.data && (
        <>
          <header className="campaign-detail__head">
            <h2 className="campaign-detail__title">{campaign.data.title}</h2>
            <p className="campaign-detail__key mono">{campaign.data.key}</p>
          </header>

          {campaign.data.description && (
            <p className="campaign-detail__description">{campaign.data.description}</p>
          )}

          {start.isError && (
            <p className="campaign-detail__blocked" role="alert">
              {start.error.message}
            </p>
          )}

          {members.length === 0 ? (
            <EmptyState
              title="No tests in this campaign"
              message="Add a suite to the campaign's suite directory, then rescan."
            />
          ) : (
            <table className="campaign-detail__members">
              <thead>
                <tr>
                  <th scope="col">Test</th>
                  {show.component && <th scope="col">Component</th>}
                  {show.fixture && <th scope="col">Fixture</th>}
                  <th scope="col">Profile</th>
                  <th scope="col">
                    <span className="campaign-detail__sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {members.map((member) => (
                  <MemberRow
                    key={member.suite}
                    busy={start.isPending}
                    member={member}
                    onRun={(suite) => start.mutate(suite)}
                    show={show}
                  />
                ))}
              </tbody>
            </table>
          )}

          <p className="campaign-detail__path mono">{campaign.data.suites_dir}</p>
        </>
      )}
    </section>
  );
};

export default CampaignDetail;
