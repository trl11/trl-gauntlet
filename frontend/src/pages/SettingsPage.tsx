import { useQuery } from "@tanstack/react-query";
import { Spinner } from "@trl11/components/ui";

import { apiUrl, getHealth, getSettings, getSystemInfo } from "@api/client";
import DefinitionRows, { type DefinitionRow } from "@components/DefinitionRows";
import PageHeader from "@components/PageHeader";
import Panel from "@components/Panel";

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

/** How the health probe reads: its wording, and whether it is a fault. */
function healthStatus(pending: boolean, healthy: boolean): { failed: boolean; label: string } {
  if (healthy) return { failed: false, label: "healthy" };
  if (pending) return { failed: false, label: "checking" };
  return { failed: true, label: "unreachable" };
}

/** What the service is and where it answers. */
export const SettingsPage: React.FC = () => {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: HEALTH_POLL_MS,
    retry: false,
  });
  const info = useQuery({ queryKey: ["system-info"], queryFn: getSystemInfo });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });

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
      </div>
    </div>
  );
};

export default SettingsPage;
