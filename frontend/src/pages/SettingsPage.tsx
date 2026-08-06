import { useQuery } from "@tanstack/react-query";
import { Spinner } from "@trl11/components/ui";

import { apiUrl, getHealth, getSettings, getSystemData, getSystemInfo } from "@api/client";
import type { SystemData, SystemTemperature } from "@api/types";
import DefinitionRows, { type DefinitionRow } from "@components/DefinitionRows";
import PageHeader from "@components/PageHeader";
import Panel from "@components/Panel";
import { formatBytes, formatDuration, formatPercent } from "../utils/format";

import "./SettingsPage.scss";

/** How often the health probe is repeated. */
const HEALTH_POLL_MS = 5000;
/** How often host telemetry is re-read. */
const HOST_POLL_MS = 3000;

function hottest(temperatures: SystemTemperature[]): SystemTemperature | null {
  if (temperatures.length === 0) return null;
  return [...temperatures].sort((a, b) => b.celsius - a.celsius)[0];
}

/**
 * The host figures, as label and value.
 *
 * The disk is the one this Gauntlet writes its runs to, named by the server:
 * a container bind-mounts one volume under several names, so the fullest mount
 * is an accurate reading under an arbitrary label. Only the hottest sensor is
 * named, that being the one closest to a limit.
 */
function hostRows(data: SystemData): DefinitionRow[] {
  const disk = data.disk ?? null;
  const thermal = hottest(data.temperatures);
  return [
    {
      label: "cpu",
      // Null until a second sample has been taken to measure against.
      value: data.cpu_percent == null ? "sampling" : formatPercent(data.cpu_percent),
    },
    { label: "cores", value: data.cpu_per_core.length ? String(data.cpu_per_core.length) : "-" },
    {
      label: "load average",
      value: data.load_avg ? data.load_avg.map((one) => one.toFixed(2)).join("  ") : "-",
    },
    {
      label: "memory",
      value: `${formatPercent(data.memory.percent)} · ${formatBytes(data.memory.used)} of ${formatBytes(data.memory.total)}`,
    },
    {
      label: "disk",
      value: disk
        ? `${formatPercent(disk.percent)} · ${formatBytes(disk.free)} free on ${disk.mount}`
        : "-",
    },
    {
      label: "hottest zone",
      value: thermal ? `${thermal.celsius.toFixed(1)} °C · ${thermal.label}` : "no sensors",
    },
    { label: "processes", value: data.process_count == null ? "-" : String(data.process_count) },
    { label: "uptime", value: formatDuration(data.uptime_s) },
  ];
}

/** Render any settings value as a single line. */
function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.length === 0 ? "-" : value.join("\n");
  return String(value);
}

/** How the health probe reads: its wording, and whether it is a fault. */
function healthStatus(pending: boolean, healthy: boolean): { failed: boolean; label: string } {
  if (healthy) return { failed: false, label: "healthy" };
  if (pending) return { failed: false, label: "checking" };
  return { failed: true, label: "unreachable" };
}

/** What the service is, where it answers, and how the host carrying it is doing. */
export const SettingsPage: React.FC = () => {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: HEALTH_POLL_MS,
    retry: false,
  });
  const info = useQuery({ queryKey: ["system-info"], queryFn: getSystemInfo });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const system = useQuery({
    queryKey: ["system", "data"],
    queryFn: getSystemData,
    refetchInterval: HOST_POLL_MS,
  });

  const status = healthStatus(health.isPending, health.isSuccess);
  const config = settings.data;
  const host = info.data;

  // The health probe answers on its own, so its row is drawn whatever the
  // settings query behind the rest of the section is doing.
  const runtime: DefinitionRow[] = [
    {
      label: "api server",
      value: (
        <span aria-live="polite" className={status.failed ? "settings-page__fault" : undefined}>
          {status.label}
        </span>
      ),
    },
  ];
  if (config) {
    runtime.push(
      { label: "port", value: text(config.port) },
      { label: "log level", value: text(config.log_level) }
    );
  }

  return (
    <div className="settings-page">
      <PageHeader title="Settings" />

      <div className="settings-page__grid">
        <Panel>
          <div className="settings-page__section">
            <h3 className="settings-page__section-title">Info</h3>
            {info.isPending ? (
              <Spinner />
            ) : info.isError ? (
              <p className="settings-page__error" role="alert">
                {info.error.message}
              </p>
            ) : (
              <DefinitionRows
                rows={[
                  { label: "hostname", value: text(host?.hostname) },
                  { label: "gauntlet", value: text(host?.gauntlet) },
                  { label: "gauntlet sdk", value: text(host?.gauntlet_sdk) },
                ]}
              />
            )}
          </div>

          <div className="settings-page__section">
            <h3 className="settings-page__section-title">Runtime</h3>
            <DefinitionRows rows={runtime} />
            {settings.isPending && <Spinner />}
            {settings.isError && (
              <p className="settings-page__error" role="alert">
                {settings.error.message}
              </p>
            )}
          </div>

          <div className="settings-page__section">
            <h3 className="settings-page__section-title">Documentation</h3>
            <DefinitionRows
              rows={[
                {
                  label: "api documentation",
                  value: (
                    <a
                      className="panel__action"
                      href={apiUrl("/docs")}
                      target="_blank"
                      rel="noreferrer"
                    >
                      swagger
                    </a>
                  ),
                },
              ]}
            />
          </div>
        </Panel>

        <Panel>
          <div className="settings-page__section">
            <h3 className="settings-page__section-title">Host stats</h3>
            {system.isPending ? (
              <Spinner />
            ) : system.isError ? (
              <p className="settings-page__error" role="alert">
                Host telemetry is unavailable.
              </p>
            ) : (
              <DefinitionRows rows={hostRows(system.data)} />
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
};

export default SettingsPage;
