import {
  faBars,
  faChartLine,
  faClockRotateLeft,
  faGear,
  faMicrochip,
  faPlay,
  faSliders,
  faXmark,
} from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@trl11/components/ui";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router";

import { getVersion, listRuns } from "@api/client";
import type { RunRow } from "@api/types";
import logo from "@assets/logo.svg";
import ApiErrorBanner from "@components/ApiErrorBanner";
import ErrorBoundary from "@components/ErrorBoundary";
import ShortcutsHelp from "@components/ShortcutsHelp";
import StatusPill from "@components/StatusPill";
import useGlobalShortcuts from "@hooks/useGlobalShortcuts";
import { isLive } from "../utils/run_status";

import "./Layout.scss";

const NAV = [
  { icon: faChartLine, label: "Dashboard", path: "/" },
  { icon: faClockRotateLeft, label: "History", path: "/history" },
  { icon: faPlay, label: "Tests", path: "/tests" },
  { icon: faMicrochip, label: "Units", path: "/units" },
  { icon: faSliders, label: "Instruments", path: "/instruments" },
  { icon: faGear, label: "Settings", path: "/settings" },
];

function activeRun(runs: RunRow[] | undefined): RunRow | null {
  return runs?.find((run) => isLive(run.status)) ?? null;
}

/** The app shell: top tab bar, live-run indicator, routed page. */
export const Layout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { closeHelp, helpOpen, shortcuts } = useGlobalShortcuts();
  const [tabsOpen, setTabsOpen] = useState(false);

  const version = useQuery({ queryKey: ["version"], queryFn: getVersion, staleTime: 300_000 });
  const runs = useQuery({
    queryKey: ["runs", "active"],
    queryFn: () => listRuns({ limit: 20 }),
    refetchInterval: 5000,
  });
  const live = activeRun(runs.data?.runs);

  useEffect(() => setTabsOpen(false), [location.pathname]);

  return (
    <div className="layout">
      <a className="layout__skip" href="#main">
        Skip to content
      </a>

      <nav className="layout__bar" aria-label="Primary">
        <Link to="/" className="layout__brand">
          <img src={logo} alt="" width={26} height={26} />
          <span className="layout__wordmark">Gauntlet</span>
        </Link>

        <Button
          className="layout__tabs-toggle"
          square
          aria-label={tabsOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={tabsOpen}
          aria-controls="primary-tabs"
          onClick={() => setTabsOpen((open) => !open)}
        >
          <FontAwesomeIcon icon={tabsOpen ? faXmark : faBars} />
        </Button>

        <ul id="primary-tabs" className={clsx("layout__tabs", tabsOpen && "is-open")}>
          {NAV.map((item) => (
            <li key={item.path}>
              <NavLink
                to={item.path}
                end={item.path === "/"}
                className={({ isActive }) => clsx("layout__tab", isActive && "is-active")}
              >
                <FontAwesomeIcon icon={item.icon} aria-hidden="true" fixedWidth />
                <span>{item.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>

        <div className="layout__bar-end">
          {live && (
            <Link
              to={`/runs/${encodeURIComponent(live.run_id)}`}
              className="layout__active-run"
              aria-live="polite"
            >
              <StatusPill status={live.status} />
              <span className="layout__active-run-suite">{live.suite}</span>
            </Link>
          )}

          <Button color="blue" size="small" onClick={() => navigate("/tests")}>
            <FontAwesomeIcon icon={faPlay} />
            Run a test
          </Button>

          {version.data && <span className="layout__version">{`v${version.data.gauntlet}`}</span>}
        </div>
      </nav>

      {tabsOpen && (
        <div className="layout__backdrop" aria-hidden="true" onClick={() => setTabsOpen(false)} />
      )}

      <main id="main" className="layout__main" tabIndex={-1}>
        <ApiErrorBanner />
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>

      {helpOpen && <ShortcutsHelp shortcuts={shortcuts} onClose={closeHelp} />}
    </div>
  );
};

export default Layout;
