import { faRotate } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Spinner } from "@trl11/components/ui";
import clsx from "clsx";
import { useId, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { listInstruments, listSuites, rescanSuites, verifySuite } from "@api/client";
import type { Instrument, Suite, SuiteList, VerifyReport } from "@api/types";
import EmptyState from "@components/EmptyState";
import PageHeader from "@components/PageHeader";
import ProfileEditor from "@components/ProfileEditor";
import RunStartModal from "@components/RunStartModal";
import SuiteDetail from "@components/SuiteDetail";

import "./TestsPage.scss";

/** Requirements of this suite that no available instrument satisfies. */
function unmetRequirements(suite: Suite, instruments: Instrument[]): string[] {
  return suite.requires.filter((name) => {
    const instrument = instruments.find((entry) => entry.name === name);
    return instrument === undefined || !instrument.available;
  });
}

function matchesSearch(suite: Suite, search: string): boolean {
  const text = `${suite.key} ${suite.title} ${suite.category} ${suite.description}`.toLowerCase();
  return text.includes(search.trim().toLowerCase());
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
  await rescanSuites();
  const catalog = await listSuites();
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

/** The suite catalog: pick a suite, pick a profile, start a run. */
export const TestsPage: React.FC = () => {
  const searchId = useId();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<{ name: string; suite: string } | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [reports, setReports] = useState<VerifyReports>({});

  const suites = useQuery({ queryKey: ["suites"], queryFn: listSuites });
  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: listInstruments,
    refetchInterval: 15_000,
  });
  const rescan = useMutation({
    mutationFn: rescanAndVerify,
    onSuccess: (result) => {
      queryClient.setQueryData(["suites"], result.catalog);
      setReports(result.reports);
    },
  });

  const all = suites.data?.suites ?? [];
  const filtered = useMemo(
    () => all.filter((suite) => matchesSearch(suite, search)),
    [all, search]
  );
  const requested = searchParams.get("suite") ?? "";
  const selected = filtered.find((suite) => suite.key === requested) ?? filtered[0] ?? null;

  // The picked profile belongs to one suite; selecting another leaves it
  // behind rather than showing it out of context.
  const selectedProfile = picked !== null && picked.suite === selected?.key ? picked.name : null;
  const report = selected === null ? null : (reports[selected.key] ?? null);

  const select = (key: string) => setSearchParams({ suite: key }, { replace: true });
  const unmet = selected ? unmetRequirements(selected, instruments.data?.instruments ?? []) : [];

  return (
    <div className="tests-page">
      <PageHeader
        title="Tests"
        subtitle="Pick a suite, choose a profile, start a run"
        actions={
          <Button onClick={() => rescan.mutate()} disabled={rescan.isPending}>
            <FontAwesomeIcon icon={faRotate} spin={rescan.isPending} aria-hidden="true" /> Rescan
          </Button>
        }
      />

      {suites.data?.errors.map((message) => (
        <p key={message} className="tests-page__blocked" role="alert">
          {message}
        </p>
      ))}
      {suites.isError && (
        <p className="tests-page__blocked" role="alert">
          {suites.error.message}
        </p>
      )}
      {rescan.isError && (
        <p className="tests-page__blocked" role="alert">
          {rescan.error.message}
        </p>
      )}

      {suites.isLoading && <Spinner className="tests-page__spinner" />}

      {!suites.isLoading && all.length === 0 && (
        <EmptyState
          title="No suites discovered"
          message="Add a suite.yaml under a configured suite root, then rescan."
          action={<Button onClick={() => rescan.mutate()}>Rescan</Button>}
        />
      )}

      {all.length > 0 && (
        <div className="tests-page__panes">
          <aside className="tests-page__rail">
            <Input
              id={searchId}
              label="Search"
              type="search"
              placeholder="Filter suites"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            {filtered.length === 0 && <p className="muted">Nothing matches that search.</p>}
            {byCategory(filtered).map(([category, members]) => (
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
