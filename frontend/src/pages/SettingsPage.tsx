import { useQuery } from "@tanstack/react-query";
import { Badge, Spinner } from "@trl11/components/ui";

import { apiUrl, getHealth, getSettings, getSystemInfo } from "@api/client";
import DefinitionRows from "@components/DefinitionRows";
import PageHeader from "@components/PageHeader";
import Panel from "@components/Panel";
import { formatBytes, formatTimestamp } from "../utils/format";

import "./SettingsPage.scss";

/** How often the health probe is repeated. */
const HEALTH_POLL_MS = 5000;

/** Render any settings value as a single line. */
function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.length === 0 ? "-" : value.join("\n");
  return String(value);
}

/** The badge shown for the health probe: its colour and its wording. */
function healthBadge(
  pending: boolean,
  healthy: boolean
): { color: "green" | "outline" | "red"; label: string } {
  if (healthy) return { color: "green", label: "API HEALTHY" };
  if (pending) return { color: "outline", label: "CHECKING" };
  return { color: "red", label: "API UNREACHABLE" };
}

/** Settings, host telemetry, and versions. */
export const SettingsPage: React.FC = () => {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: HEALTH_POLL_MS,
    retry: false,
  });
  const info = useQuery({ queryKey: ["system-info"], queryFn: getSystemInfo });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });

  const badge = healthBadge(health.isPending, health.isSuccess);
  const config = settings.data;
  const host = info.data;

  return (
    <div className="settings-page">
      <PageHeader
        title="Settings"
        actions={
          <Badge aria-live="polite" color={badge.color}>
            {badge.label}
          </Badge>
        }
      />

      <div className="settings-page__grid">
        <Panel
          title="Service"
          action={
            <a className="panel__action" href={apiUrl("/docs")} target="_blank" rel="noreferrer">
              API documentation
            </a>
          }
        >
          {settings.isPending ? (
            <Spinner />
          ) : settings.isError ? (
            <p className="settings-page__error" role="alert">
              {settings.error.message}
            </p>
          ) : (
            <DefinitionRows
              rows={[
                { label: "host", value: text(config?.host) },
                { label: "port", value: text(config?.port) },
                { label: "log level", value: text(config?.log_level) },
                { label: "opens a browser", value: text(config?.open_browser) },
                { label: "default target", value: text(config?.default_target) },
              ]}
            />
          )}
        </Panel>

        <Panel
          title="Paths"
          action={
            <a
              className="panel__action"
              href={apiUrl("/api/schemas")}
              target="_blank"
              rel="noreferrer"
            >
              Contract schemas
            </a>
          }
        >
          <DefinitionRows
            rows={[
              { label: "data dir", value: text(config?.data_dir) },
              { label: "runs dir", value: text(config?.runs_dir) },
              { label: "profiles dir", value: text(config?.profiles_dir) },
              { label: "runs index", value: text(config?.runs_index_path) },
              { label: "suite roots", value: text(config?.suite_roots) },
            ]}
          />
        </Panel>

        <Panel title="Versions">
          {info.isPending ? (
            <Spinner />
          ) : info.isError ? (
            <p className="settings-page__error" role="alert">
              {info.error.message}
            </p>
          ) : (
            <DefinitionRows
              rows={[
                { label: "gauntlet", value: text(host?.gauntlet) },
                { label: "suite sdk", value: text(host?.gauntlet_sdk) },
                { label: "contract", value: text(host?.contract_version) },
                { label: "python", value: text(host?.python) },
                { label: "platform", value: text(host?.os) },
              ]}
            />
          )}
        </Panel>

        <Panel title="Host">
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
                { label: "operating system", value: text(host?.os) },
                { label: "kernel", value: text(host?.kernel) },
                { label: "architecture", value: text(host?.arch) },
                { label: "cpu", value: text(host?.cpu_model) },
                { label: "cores", value: text(host?.cpu_count) },
                { label: "memory", value: formatBytes(host?.memory_total_bytes) },
                { label: "booted", value: formatTimestamp(host?.boot_time) },
              ]}
            />
          )}
        </Panel>
      </div>
    </div>
  );
};

export default SettingsPage;
