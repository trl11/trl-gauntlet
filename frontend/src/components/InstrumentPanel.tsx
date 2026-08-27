import { faChevronDown, faChevronUp, faRotate, faXmark } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Button, Select } from "@trl11/components/ui";
import clsx from "clsx";
import { useEffect, useId, useRef, useState } from "react";

import type { Instrument, InstrumentCommand, InstrumentReadout } from "@api/types";
import CommandForm from "@components/CommandForm";
import CommandGroup from "@components/CommandGroup";
import InstrumentState from "@components/InstrumentState";
import SevenSegment from "@components/SevenSegment";
import Sparkline from "@components/Sparkline";
import {
  readingText,
  readoutGroups,
  toneOf,
  valueAt,
  type ReadingTone,
  type ReadoutGroup,
} from "../utils/readouts";

import "./InstrumentPanel.scss";

/** How many polls of history a reading's sparkline keeps. */
const MAX_SAMPLES = 60;

/** Where one instrument's collapsed state lives, so it survives a reload. */
const collapseKey = (instanceId: string) => `instrument-panel:collapsed:${instanceId}`;

/** Whether an operator last left this instrument's panel collapsed. */
function wasCollapsed(instanceId: string): boolean {
  try {
    return localStorage.getItem(collapseKey(instanceId)) === "1";
  } catch {
    return false;
  }
}

function rememberCollapsed(instanceId: string, collapsed: boolean): void {
  try {
    if (collapsed) localStorage.setItem(collapseKey(instanceId), "1");
    else localStorage.removeItem(collapseKey(instanceId));
  } catch {
    // A viewer with storage blocked just loses the memory of it, not the toggle.
  }
}

/**
 * The image a command answered with, and what to send for another like it.
 *
 * Any command whose result carries an image gets one of these, so nothing here
 * knows which instrument takes pictures or what its command is called.
 */
export interface InstrumentPreview {
  /** The image itself, as a data URL. */
  src: string;
}

/** Props every instrument panel takes. */
export interface InstrumentPanelProps {
  /** Disables every control while a command is in flight. */
  busy?: boolean;
  /** Message from the command that last failed. */
  error?: string | null;
  /** The instrument to draw, as the API reported it. */
  instrument: Instrument;
  /** Sends one command to this instrument. */
  onCommand: (command: string, args: Record<string, unknown>) => void | Promise<unknown>;
  /** Takes the last image off the panel. */
  onDismiss?: () => void;
  /** The last image this instrument answered with, if it has answered with one. */
  preview?: InstrumentPreview | null;
}

/**
 * One reading lit on the display, with its unit ringed the way a panel rings it.
 *
 * A reading that is on burns green whatever colour its position would give it,
 * because that is what an indicator lamp does.
 */
const Reading: React.FC<{
  history: Array<Record<string, number>>;
  readout: InstrumentReadout;
  state: Record<string, unknown>;
  tone: ReadingTone;
}> = ({ history, readout, state, tone }) => {
  const value = valueAt(state, readout.key);
  const trend = history
    .map((sample) => sample[readout.key])
    .filter((sample): sample is number => typeof sample === "number");
  return (
    <div className="instrument-panel__reading">
      <SevenSegment
        tone={value === true ? "green" : tone}
        value={readingText(value, readout.precision)}
      />
      {readout.unit && <span className="instrument-panel__unit">{readout.unit}</span>}
      <Sparkline values={trend} />
      <span className="instrument-panel__reading-label">{readout.label}</span>
    </div>
  );
};

/** One group of readouts, each reading carrying its own recent history. */
const ReadoutGroupView: React.FC<{
  busy: boolean;
  group: ReadoutGroup;
  history: Array<Record<string, number>>;
  onRefresh?: () => void;
  state: Record<string, unknown>;
}> = ({ busy, group, history, onRefresh, state }) => (
  <section className="instrument-panel__group">
    {(group.name || onRefresh) && (
      <h3 className="instrument-panel__group-name">
        {group.name}
        {onRefresh && (
          <Button
            aria-label={`Refresh ${group.name}`}
            className="instrument-panel__refresh"
            color="transparent"
            disabled={busy}
            onClick={onRefresh}
            size="small"
            type="button"
          >
            <FontAwesomeIcon icon={faRotate} spin={busy} />
          </Button>
        )}
      </h3>
    )}
    <div className="instrument-panel__display">
      {group.headline.length > 0 && (
        <div className="instrument-panel__readings">
          {group.headline.map((entry, index) => (
            <Reading
              history={history}
              key={entry.key}
              readout={entry}
              state={state}
              tone={toneOf(entry, index, group.headline.length)}
            />
          ))}
        </div>
      )}
      {group.summary.length > 0 && (
        <div className="instrument-panel__readings instrument-panel__readings--small">
          {group.summary.map((entry) => (
            <Reading
              history={history}
              key={entry.key}
              readout={entry}
              state={state}
              tone={toneOf(entry, 2, 3)}
            />
          ))}
        </div>
      )}
    </div>
  </section>
);

/** One card of the deck: a single command, or several sharing a `group`. */
type DeckItem = { command: InstrumentCommand } | { group: InstrumentCommand[] };

/**
 * Commands sharing a `group` collapse into one card, in the order they were
 * first declared; everything else keeps its own. A "group" of one is drawn
 * as a single command — grouping only pays for itself once there is more
 * than one key to press over the same controls.
 *
 * `slots` decides where each card sits in the deck — a card with no fields
 * to gather first sorts ahead of the ones that use what it finds — but a
 * group's own members always come out in `declared` order, the provider's
 * own, so reordering the deck around a fieldless command such as a detect
 * never reorders that detect past the transactions inside its own card.
 */
function deckItems(slots: InstrumentCommand[], declared: InstrumentCommand[]): DeckItem[] {
  const byGroup = new Map<string, InstrumentCommand[]>();
  for (const command of declared) {
    if (!command.group) continue;
    const members = byGroup.get(command.group) ?? [];
    members.push(command);
    byGroup.set(command.group, members);
  }
  const seen = new Set<string>();
  const items: DeckItem[] = [];
  for (const command of slots) {
    if (!command.group) {
      items.push({ command });
      continue;
    }
    if (seen.has(command.group)) continue;
    seen.add(command.group);
    const members = byGroup.get(command.group) ?? [command];
    items.push(members.length > 1 ? { group: members } : { command: members[0] });
  }
  return items;
}

/**
 * Draws any instrument from its reported state and its declared commands.
 *
 * A provider that declares `readouts` gets tiles, each with a sparkline of
 * where the reading has been, and a compact strip laid out the way it asked
 * for; one that declares none gets every state value listed as a key and a
 * value. Nothing here knows which
 * instrument it is looking at, so a provider that registers with Gauntlet gets
 * a working panel with no frontend change.
 */
export const InstrumentPanel: React.FC<InstrumentPanelProps> = ({
  busy = false,
  error,
  instrument,
  onCommand,
  onDismiss,
  preview = null,
}) => {
  const fieldId = useId();
  const [history, setHistory] = useState<Array<Record<string, number>>>([]);
  const [continuous, setContinuous] = useState(false);
  const [live, setLive] = useState(false);
  // Collapsed by the operator to get an instrument they are not using out of
  // the way; remembered per instrument, so the bench stays tidy across a
  // reload rather than reopening everything.
  const [collapsed, setCollapsed] = useState(() => wasCollapsed(instrument.instance_id));

  useEffect(() => {
    rememberCollapsed(instrument.instance_id, collapsed);
  }, [collapsed, instrument.instance_id]);
  // What the viewer's own controls are set to. A field the provider declared
  // with choices is a preset, and starts on the first of them.
  const [settings, setSettings] = useState<Record<string, string>>({});

  // Read through a ref so that a page re-rendering on its poll does not count
  // as a reason to ask for another image.
  const send = useRef(onCommand);
  useEffect(() => {
    send.current = onCommand;
  });

  useEffect(() => {
    const sample: Record<string, number> = {};
    for (const entry of instrument.readouts ?? []) {
      const value = valueAt(instrument.state, entry.key);
      if (entry.role !== "summary" && typeof value === "number") sample[entry.key] = value;
    }
    if (Object.keys(sample).length === 0) return;
    setHistory((current) => [...current, sample].slice(-MAX_SAMPLES));
  }, [instrument]);

  const disabled = busy || !instrument.available;
  // The command that answers with a picture, which the viewer takes over from
  // the deck so its button can say what pressing it will do.
  const viewer = instrument.commands.find((command) => command.returns === "image") ?? null;
  const presets = (viewer?.fields ?? []).filter((field) => field.choices.length > 0);
  // Readings the provider pinned to the viewer, which sit with its controls
  // rather than on a display of their own.
  const pinned = (instrument.readouts ?? []).filter((entry) => entry.role === "viewer");

  useEffect(() => {
    setSettings(Object.fromEntries(presets.map((field) => [field.name, field.choices[0]])));
    // Only the shape of what is on offer decides where the controls start.
  }, [presets.map((field) => `${field.name}:${field.choices.join()}`).join("|")]);

  // A command that failed stops the loop rather than being retried forever.
  useEffect(() => {
    if (error) setLive(false);
  }, [error]);

  // The next image is asked for once the last one has arrived, so a camera
  // slower than any interval sets its own pace instead of queueing commands.
  useEffect(() => {
    if (!live || busy || disabled || viewer === null) return;
    // `live` tells the provider the picture is being refreshed rather than
    // kept, so it may answer with something cheaper to send.
    void send.current(viewer.name, { ...settings, live: true });
  }, [busy, disabled, live, preview, settings, viewer]);

  const inUse = Boolean(instrument.in_use_by);
  const groups = readoutGroups(instrument.readouts ?? []);
  // A group with nothing to burn large is describing how the instrument is
  // configured rather than reporting what it is doing, so it belongs with the
  // rest of the identifying detail rather than taking a display of its own.
  const specs = groups.filter((group) => group.headline.length === 0);
  const displays = groups.filter((group) => group.headline.length > 0);
  // A command that only brings a group up to date is drawn in that group's
  // heading, so it is where the readings it refreshes are.
  const refreshers = new Map(
    instrument.commands
      .filter((command) => command.refreshes !== undefined && command.fields.length === 0)
      .map((command) => [command.refreshes as string, command])
  );
  const others = instrument.commands.filter(
    (command) => command !== viewer && !refreshers.has(command.refreshes ?? "")
  );
  const rest = others.filter((command) => command.name !== instrument.primary_command);
  const primary = others.find((command) => command.name === instrument.primary_command);
  const footer = rest.filter((command) => command.fields.length === 0);
  const rows = rest.filter((command) => command.fields.length > 0);
  const deck = deckItems([...footer, ...rows, ...(primary ? [primary] : [])], others);
  const subtitle = [instrument.instance_id, instrument.connection].filter(Boolean).join(" · ");

  return (
    <div className="instrument-panel">
      <header className="instrument-panel__head">
        <div className="instrument-panel__titles">
          <h2 className="instrument-panel__name">{instrument.name}</h2>
          <p className="instrument-panel__sub">{subtitle}</p>
          {specs.length > 0 && (
            <dl className="instrument-panel__spec">
              {specs
                .flatMap((group) => group.summary)
                .map((entry) => (
                  <div className="instrument-panel__spec-item" key={entry.key}>
                    <dt>{entry.label}</dt>
                    <dd>
                      {readingText(valueAt(instrument.state, entry.key), entry.precision)}
                      {entry.unit && ` ${entry.unit}`}
                    </dd>
                  </div>
                ))}
            </dl>
          )}
        </div>
        <div className="instrument-panel__status">
          <span
            aria-live="polite"
            className={clsx(
              "instrument-panel__chip",
              !inUse && instrument.available && "instrument-panel__chip--on",
              inUse && "instrument-panel__chip--busy"
            )}
            title={
              inUse
                ? `run ${instrument.in_use_by} is driving this instrument; its key stays locked until the run ends`
                : undefined
            }
          >
            <span className="instrument-panel__dot" aria-hidden="true" />
            {!instrument.available ? "UNAVAILABLE" : inUse ? "IN USE" : "AVAILABLE"}
          </span>
          <Button
            aria-expanded={!collapsed}
            aria-label={collapsed ? `Expand ${instrument.name}` : `Collapse ${instrument.name}`}
            className="instrument-panel__collapse"
            color="transparent"
            onClick={() => setCollapsed((current) => !current)}
            size="small"
            type="button"
          >
            <FontAwesomeIcon icon={collapsed ? faChevronDown : faChevronUp} />
          </Button>
        </div>
      </header>

      {collapsed && (
        <p className="instrument-panel__collapsed-hint">
          Collapsed
          {specs.length === 0
            ? ""
            : " · " +
              specs
                .flatMap((group) => group.summary)
                .map(
                  (entry) =>
                    `${entry.label} ${readingText(valueAt(instrument.state, entry.key), entry.precision)}${entry.unit ? ` ${entry.unit}` : ""}`
                )
                .join(" · ")}
        </p>
      )}

      {!collapsed && instrument.description && (
        <p className="instrument-panel__description">{instrument.description}</p>
      )}

      {!collapsed && groups.length === 0 && <InstrumentState state={instrument.state} />}

      {!collapsed && displays.length > 0 && (
        <div className="instrument-panel__groups">
          {displays.map((group) => {
            const refresher = refreshers.get(group.name);
            return (
              <ReadoutGroupView
                busy={busy}
                group={group}
                history={history}
                key={group.name}
                onRefresh={refresher && !disabled ? () => onCommand(refresher.name, {}) : undefined}
                state={instrument.state}
              />
            );
          })}
        </div>
      )}

      {!collapsed && !instrument.available && (
        <p className="instrument-panel__unavailable">
          {instrument.unavailable_reason ||
            "the provider reports this instrument as unavailable; controls are read-only"}
        </p>
      )}

      {!collapsed && instrument.commands.length === 0 && (
        <p className="instrument-panel__quiet">Takes no commands.</p>
      )}

      {!collapsed && (
        <>
          <div className="instrument-panel__deck">
            {/* Every non-latching command gets the same bordered card,
                whether or not it takes fields, so a lone button such as a
                scan reads as one command among the others rather than a
                stray control. A command with no fields to gather first —
                a scan, a detect — sorts ahead of the ones that use what it
                finds, and the primary command sorts last, same as before
                commands could share a card. Commands the provider marked
                with the same `group` — a write and a read of the same
                address — collapse into one card, so their shared fields are
                entered once rather than once per command. */}
            {deck.length > 0 && (
              <div className="instrument-panel__modules">
                {deck.map((item) => {
                  if ("group" in item) {
                    return (
                      <CommandGroup
                        commands={item.group}
                        disabled={disabled}
                        key={item.group.map((command) => command.name).join("+")}
                        onSubmit={(command, args) => onCommand(command.name, args)}
                        state={instrument.state}
                      />
                    );
                  }
                  const { command } = item;
                  const isPrimary = command === primary;
                  return (
                    <CommandForm
                      command={command}
                      disabled={disabled}
                      held={isPrimary ? Boolean(instrument.in_use_by) : undefined}
                      key={command.name}
                      onSubmit={(args) => onCommand(command.name, args)}
                      primary={isPrimary || undefined}
                      state={instrument.state}
                    />
                  );
                })}
              </div>
            )}
          </div>

          {viewer !== null && (
            <section className="instrument-panel__view">
              <div className="instrument-panel__modes">
                <Button
                  aria-pressed={!continuous}
                  className={clsx(
                    "instrument-panel__mode",
                    !continuous && "instrument-panel__mode--on"
                  )}
                  color="transparent"
                  onClick={() => {
                    setContinuous(false);
                    setLive(false);
                  }}
                  size="small"
                  type="button"
                >
                  Snapshot
                </Button>
                <Button
                  aria-pressed={continuous}
                  className={clsx(
                    "instrument-panel__mode",
                    continuous && "instrument-panel__mode--on"
                  )}
                  color="transparent"
                  onClick={() => setContinuous(true)}
                  size="small"
                  type="button"
                >
                  Continuous
                </Button>
                {presets.map((field) => (
                  <Select
                    aria-label={field.unit ? `${field.label} (${field.unit})` : field.label}
                    className="instrument-panel__preset"
                    id={`${fieldId}-${field.name}`}
                    key={field.name}
                    onChange={(event) =>
                      setSettings((current) => ({ ...current, [field.name]: event.target.value }))
                    }
                    options={field.choices.map((choice) => ({ value: choice, label: choice }))}
                    value={settings[field.name] ?? field.choices[0]}
                  />
                ))}
                {pinned.map((entry) => (
                  <span className="instrument-panel__pinned" key={entry.key}>
                    <span className="instrument-panel__pinned-label">{entry.label}</span>
                    {readingText(valueAt(instrument.state, entry.key), entry.precision)}
                    {entry.unit && ` ${entry.unit}`}
                  </span>
                ))}
              </div>

              <Button
                className={clsx("instrument-panel__go", live && "instrument-panel__go--on")}
                color="transparent"
                disabled={!instrument.available || (busy && !live)}
                onClick={() => {
                  if (!continuous) void send.current(viewer.name, settings);
                  else setLive((running) => !running);
                }}
                size="small"
                type="button"
              >
                {!continuous ? "Capture" : live ? "Stop" : "Start"}
              </Button>

              {preview !== null && (
                <div className="instrument-panel__shot">
                  <img
                    alt={`the last image ${instrument.name} answered with`}
                    className="instrument-panel__frame"
                    src={preview.src}
                  />
                  {onDismiss !== undefined && (
                    <Button
                      aria-label="Hide"
                      className="instrument-panel__close"
                      color="transparent"
                      onClick={() => {
                        setLive(false);
                        onDismiss();
                      }}
                      size="small"
                      type="button"
                    >
                      <FontAwesomeIcon icon={faXmark} />
                    </Button>
                  )}
                </div>
              )}
            </section>
          )}

          {error && (
            <p className="instrument-panel__error" role="alert">
              {error}
            </p>
          )}
        </>
      )}
    </div>
  );
};

export default InstrumentPanel;
