import { faRotate } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Spinner } from "@trl11/components/ui";
import clsx from "clsx";
import { useState } from "react";
import { useSearchParams } from "react-router";

import {
  listCampaigns,
  listInstruments,
  listSuites,
  rescanCampaigns,
  rescanSuites,
  verifySuite,
} from "@api/client";
import type { Instrument, Suite, SuiteList, VerifyReport } from "@api/types";
import CampaignDetail from "@components/CampaignDetail";
import EmptyState from "@components/EmptyState";
import PageHeader from "@components/PageHeader";
import ProfileEditor from "@components/ProfileEditor";
import RunStartModal from "@components/RunStartModal";
import SuiteDetail from "@components/SuiteDetail";
import useRememberedSearch from "@hooks/useRememberedSearch";

import "./TestsPage.scss";

/** What the rail lists: every suite, or the campaigns that group them. */
type View = "campaigns" | "suites";

/** Where the last visit's view and selection are kept. */
const REMEMBERED = "gauntlet.tests.search";
/** The parameters worth carrying from one visit to the next. */
const REMEMBERED_KEYS = ["campaign", "suite", "view"];

/** Requirements of this suite that no available instrument satisfies. */
function unmetRequirements(suite: Suite, instruments: Instrument[]): string[] {
  return suite.requires.filter((name) => {
    const instrument = instruments.find((entry) => entry.name === name);
    return instrument === undefined || !instrument.available;
  });
}

/** Every suite's conformance report, keyed by suite key. */
type VerifyReports = Record<string, VerifyReport>;

/**
 * Rediscover the suite roots, then verify every suite that was found.
 *
 * The catalog is returned alongside the reports so the caller can seat both at
 * once, rather than showing reports against the suites of the previous scan.
 */
async function rescanAndVerify(): Promise<{ catalog: SuiteList; reports: VerifyReports }> {
  const catalog = await rescanSuites();
  const reports = await Promise.all(catalog.suites.map((suite) => verifySuite(suite.key)));
  return {
    catalog,
    reports: Object.fromEntries(reports.map((report) => [report.suite, report])),
  };
}

/** Suites grouped by category, both the groups and their members sorted by name. */
function byCategory(suites: Suite[]): Array<[string, Suite[]]> {
  const groups = new Map<string, Suite[]>();
  for (const suite of suites) {
    const bucket = groups.get(suite.category) ?? [];
    bucket.push(suite);
    groups.set(suite.category, bucket);
  }
  const grouped = [...groups.entries()];
  for (const [, members] of grouped) members.sort((a, b) => a.title.localeCompare(b.title));
  grouped.sort((a, b) => a[0].localeCompare(b[0]));
  return grouped;
}

/**
 * The suite catalog, listed on its own or by the campaign that groups it.
 *
 * Both views read the same suites: a campaign contributes its suite directory
 * to discovery, so every member of one is also in the suite list. The choice is
 * only whether to reach a test through its programme or on its own.
 */
export const TestsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  // Reaching Tests from the navigation bar returns to the view and selection
  // last left, so running a campaign's suites one after another does not mean
  // choosing the campaign again each time.
  useRememberedSearch(REMEMBERED, REMEMBERED_KEYS);
  const [picked, setPicked] = useState<{ name: string; suite: string } | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [reports, setReports] = useState<VerifyReports>({});

  const view: View = searchParams.get("view") === "campaigns" ? "campaigns" : "suites";

  const suites = useQuery({ queryKey: ["suites"], queryFn: listSuites });
  const campaigns = useQuery({
    queryKey: ["campaigns"],
    queryFn: listCampaigns,
    enabled: view === "campaigns",
  });
  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: listInstruments,
    refetchInterval: 15_000,
  });

  const rescanSuiteCatalog = useMutation({
    mutationFn: rescanAndVerify,
    onSuccess: (result) => {
      queryClient.setQueryData(["suites"], result.catalog);
      setReports(result.reports);
    },
  });
  // The server does the same work either way; only the answer differs. A
  // campaign edit can change the suite catalog, so both are seated.
  const rescanCampaignCatalog = useMutation({
    mutationFn: rescanCampaigns,
    onSuccess: (result) => {
      queryClient.setQueryData(["campaigns"], result);
      queryClient.invalidateQueries({ queryKey: ["campaign"] });
      queryClient.invalidateQueries({ queryKey: ["suites"] });
    },
  });
  const rescan = view === "campaigns" ? rescanCampaignCatalog : rescanSuiteCatalog;

  const allSuites = suites.data?.suites ?? [];
  const allCampaigns = campaigns.data?.campaigns ?? [];

  const requestedSuite = searchParams.get("suite") ?? "";
  const selected = allSuites.find((suite) => suite.key === requestedSuite) ?? allSuites[0] ?? null;

  const requestedCampaign = searchParams.get("campaign") ?? "";
  const selectedCampaign =
    allCampaigns.find((entry) => entry.key === requestedCampaign)?.key ??
    allCampaigns[0]?.key ??
    null;

  // The picked profile belongs to one suite; selecting another leaves it
  // behind rather than showing it out of context.
  const selectedProfile = picked !== null && picked.suite === selected?.key ? picked.name : null;
  const report = selected === null ? null : (reports[selected.key] ?? null);

  const show = (next: View) => {
    const params = new URLSearchParams(searchParams);
    params.set("view", next);
    setSearchParams(params, { replace: true });
  };
  const select = (key: string) => {
    const params = new URLSearchParams(searchParams);
    params.set(view === "campaigns" ? "campaign" : "suite", key);
    setSearchParams(params, { replace: true });
  };

  const unmet = selected ? unmetRequirements(selected, instruments.data?.instruments ?? []) : [];
  const listing = view === "campaigns" ? campaigns : suites;
  const empty = view === "campaigns" ? allCampaigns.length === 0 : allSuites.length === 0;

  return (
    <div className="tests-page">
      <PageHeader
        title="Tests"
        actions={
          <>
            <div className="tests-page__views" role="tablist" aria-label="Group tests by">
              <Button
                type="button"
                role="tab"
                size="small"
                color={view === "suites" ? "blue" : "outline"}
                aria-selected={view === "suites"}
                onClick={() => show("suites")}
              >
                All tests
              </Button>
              <Button
                type="button"
                role="tab"
                size="small"
                color={view === "campaigns" ? "blue" : "outline"}
                aria-selected={view === "campaigns"}
                onClick={() => show("campaigns")}
              >
                Campaigns
              </Button>
            </div>
            <Button onClick={() => rescan.mutate()} disabled={rescan.isPending}>
              <FontAwesomeIcon icon={faRotate} spin={rescan.isPending} aria-hidden="true" /> Rescan
            </Button>
          </>
        }
      />

      {listing.data?.errors.map((message) => (
        <p key={message} className="tests-page__blocked" role="alert">
          {message}
        </p>
      ))}
      {listing.isError && (
        <p className="tests-page__blocked" role="alert">
          {listing.error.message}
        </p>
      )}
      {rescan.isError && (
        <p className="tests-page__blocked" role="alert">
          {rescan.error.message}
        </p>
      )}

      {listing.isLoading && <Spinner className="tests-page__spinner" />}

      {!listing.isLoading && empty && view === "suites" && (
        <EmptyState
          title="No suites discovered"
          message="Add a suite.yaml under a configured suite root, then rescan."
          action={<Button onClick={() => rescan.mutate()}>Rescan</Button>}
        />
      )}

      {!listing.isLoading && empty && view === "campaigns" && (
        <EmptyState
          title="No campaigns discovered"
          message="Add a campaign.yaml under a configured campaign root, then rescan."
          action={<Button onClick={() => rescan.mutate()}>Rescan</Button>}
        />
      )}

      {view === "suites" && allSuites.length > 0 && (
        <div className="tests-page__panes">
          <aside className="tests-page__rail">
            {byCategory(allSuites).map(([category, members]) => (
              <div key={category} className="tests-page__group">
                <p className="tests-page__group-name">{category}</p>
                {members.map((suite) => (
                  <button
                    key={suite.key}
                    type="button"
                    className={clsx(
                      "tests-page__rail-item",
                      suite.key === selected?.key && "tests-page__rail-item--active"
                    )}
                    aria-current={suite.key === selected?.key ? "true" : undefined}
                    onClick={() => select(suite.key)}
                  >
                    {suite.title}
                  </button>
                ))}
              </div>
            ))}
          </aside>

          {selected && (
            <SuiteDetail
              onEditProfile={setEditing}
              onSelectProfile={(name) => setPicked({ name, suite: selected.key })}
              onStart={() => setStarting(true)}
              selectedProfile={selectedProfile}
              suite={selected}
              unmet={unmet}
              verify={report}
            />
          )}
        </div>
      )}

      {view === "campaigns" && allCampaigns.length > 0 && (
        <div className="tests-page__panes">
          <aside className="tests-page__rail">
            {allCampaigns.map((entry) => (
              <button
                key={entry.key}
                type="button"
                className={clsx(
                  "tests-page__rail-item",
                  entry.key === selectedCampaign && "tests-page__rail-item--active"
                )}
                aria-current={entry.key === selectedCampaign ? "true" : undefined}
                onClick={() => select(entry.key)}
              >
                <span className="tests-page__rail-label">{entry.title}</span>
                <span className="tests-page__rail-count">{entry.member_count} tests</span>
              </button>
            ))}
          </aside>

          {selectedCampaign && <CampaignDetail campaignKey={selectedCampaign} />}
        </div>
      )}

      {starting && selected && (
        <RunStartModal
          initialProfile={selectedProfile}
          onClose={() => setStarting(false)}
          suite={selected}
        />
      )}

      {editing && selected && (
        <ProfileEditor
          name={editing}
          onClose={() => setEditing(null)}
          onProfileChanged={setEditing}
          suiteKey={selected.key}
        />
      )}
    </div>
  );
};

export default TestsPage;
