import { Checkbox, Input } from "@trl11/components/ui";

import type { StreamChannel } from "@api/types";
import type { DaqSettings } from "../utils/daq_limits";

import "./DaqLimits.scss";

/** Props for {@link DaqLimits}. */
export interface DaqLimitsProps {
  /** Channels the instrument streams, with the range each can read. */
  channels: StreamChannel[];
  /** Set while a request is in flight. */
  disabled?: boolean;
  /** Prefix that keeps the field ids of two instruments apart. */
  idPrefix: string;
  /** Called with the settings as changed. */
  onChange: (next: DaqSettings) => void;
  /** Offer a checkbox to put each channel in or out of the scan list. */
  selectable?: boolean;
  /** What has been typed. */
  value: DaqSettings;
}

/**
 * A low and a high limit per channel, and the seconds kept either side of a
 * crossing.
 *
 * Every channel starts at the most and the least the instrument can read, so
 * nothing is watched until a limit is moved inside that range. Nothing here
 * names an instrument or a channel: both come from what the stream reports.
 */
export const DaqLimits: React.FC<DaqLimitsProps> = ({
  channels,
  disabled = false,
  idPrefix,
  onChange,
  selectable = false,
  value,
}) => {
  const on = (key: string) => value.enabled[key] ?? true;
  return (
    <div className="daq-limits">
      <table className="daq-limits__table">
        <thead>
          <tr>
            {selectable && <th>On</th>}
            <th>Channel</th>
            <th>Low</th>
            <th>High</th>
          </tr>
        </thead>
        <tbody>
          {channels.map((channel) => (
            <tr key={channel.key}>
              {selectable && (
                <td className="daq-limits__on">
                  <Checkbox
                    aria-label={`${channel.label} enabled`}
                    checked={on(channel.key)}
                    disabled={disabled}
                    id={`${idPrefix}-${channel.key}-on`}
                    onChange={(event) =>
                      onChange({
                        ...value,
                        enabled: { ...value.enabled, [channel.key]: event.target.checked },
                      })
                    }
                  />
                </td>
              )}
              <td>
                {channel.label} <span className="daq-limits__unit">{channel.unit}</span>
              </td>
              {(["low", "high"] as const).map((side) => (
                <td key={side}>
                  <Input
                    aria-label={`${channel.label} ${side} limit`}
                    disabled={disabled || !on(channel.key)}
                    id={`${idPrefix}-${channel.key}-${side}`}
                    inputMode="decimal"
                    onChange={(event) =>
                      onChange({
                        ...value,
                        entries: {
                          ...value.entries,
                          [channel.key]: {
                            ...(value.entries[channel.key] ?? { high: "", low: "" }),
                            [side]: event.target.value,
                          },
                        },
                      })
                    }
                    value={value.entries[channel.key]?.[side] ?? ""}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="daq-limits__window">
        <Input
          disabled={disabled}
          id={`${idPrefix}-before`}
          label="Before (s)"
          onChange={(event) => onChange({ ...value, before: event.target.value })}
          value={value.before}
        />
        <Input
          disabled={disabled}
          id={`${idPrefix}-after`}
          label="After (s)"
          onChange={(event) => onChange({ ...value, after: event.target.value })}
          value={value.after}
        />
      </div>
    </div>
  );
};

export default DaqLimits;
