import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
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
import { Link, Outlet, useLocation, useNavigate } from "react-router";

import { getSystemInfo, listRuns } from "@api/client";
import type { RunRow } from "@api/types";
import logo from "@assets/logo.svg";
import ApiErrorBanner from "@components/ApiErrorBanner";
import ErrorBoundary from "@components/ErrorBoundary";
import ShortcutsHelp from "@components/ShortcutsHelp";
import StatusPill from "@components/StatusPill";
import useGlobalShortcuts from "@hooks/useGlobalShortcuts";
import { isLive } from "../utils/run_status";

import "./Layout.scss";

/** One tab: where it goes, and any route outside its own subtree it owns. */
interface NavItem {
  icon: IconDefinition;
  label: string;
  owns?: string;
  path: string;
}

const NAV: NavItem[] = [
  { icon: faChartLine, label: "Dashboard", path: "/" },
  { icon: faClockRotateLeft, label: "History", owns: "/runs/", path: "/history" },
  { icon: faPlay, label: "Tests", path: "/tests" },
  { icon: faMicrochip, label: "Units", path: "/units" },
  { icon: faSliders, label: "Instruments", path: "/instruments" },
  { icon: faGear, label: "Settings", path: "/settings" },
];

function activeRun(runs: RunRow[] | undefined): RunRow | null {
  return runs?.find((run) => isLive(run.status)) ?? null;
}

/**
 * Whether a tab owns a path: its own route and anything below it, plus the
 * route it claims. A run view is reached from anywhere, so History holds it.
 */
function isTabActive(item: NavItem, pathname: string): boolean {
  if (item.owns && pathname.startsWith(item.owns)) return true;
  if (item.path === "/") return pathname === "/";
  return pathname === item.path || pathname.startsWith(`${item.path}/`);
}

/** The app shell: top tab bar, live-run indicator, routed page. */
export const Layout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { closeHelp, helpOpen, shortcuts } = useGlobalShortcuts();
  const [tabsOpen, setTabsOpen] = useState(false);

  const version = useQuery({
    queryKey: ["system-info"],
    queryFn: getSystemInfo,
    staleTime: 300_000,
  });
  const runs = useQuery({
    queryKey: ["runs", "active"],
    queryFn: () => listRuns({ limit: 20 }),
    refetchInterval: 5000,
  });
  const live = activeRun(runs.data?.runs);
  const current = NAV.find((item) => isTabActive(item, location.pathname));

  useEffect(() => setTabsOpen(false), [location.pathname]);

  return (
    <div className="layout">
      <a className="layout__skip" href="#main">
        Skip to content
      </a>

      <nav className="layout__bar" aria-label="Primary">
        <div className="layout__brand-group">
          <Link to="/" className="layout__brand">
            <img className="layout__mark" src={logo} alt="" />
            <span className="layout__wordmark">Gauntlet</span>
          </Link>
          {version.data && <span className="layout__version">{`v${version.data.gauntlet}`}</span>}
        </div>

        {current && <span className="layout__current-page">{current.label}</span>}

        <Button
          className="layout__tabs-toggle"
          aria-label={tabsOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={tabsOpen}
          aria-controls="primary-tabs"
          onClick={() => setTabsOpen((open) => !open)}
        >
          <FontAwesomeIcon icon={tabsOpen ? faXmark : faBars} aria-hidden="true" />
          <span>{tabsOpen ? "Close" : "Menu"}</span>
        </Button>

        <ul id="primary-tabs" className={clsx("layout__tabs", tabsOpen && "is-open")}>
          {NAV.map((item) => {
            const active = isTabActive(item, location.pathname);
            return (
              <li key={item.path}>
                <Link
                  to={item.path}
                  aria-current={active ? "page" : undefined}
                  className={clsx("layout__tab", active && "is-active")}
                >
                  <FontAwesomeIcon icon={item.icon} aria-hidden="true" fixedWidth />
                  <span>{item.label}</span>
                </Link>
              </li>
            );
          })}
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
