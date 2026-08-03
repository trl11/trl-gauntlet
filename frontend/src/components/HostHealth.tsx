import type { SystemData, SystemDisk, SystemTemperature } from "@api/types";
import StatTile, { type StatTone } from "@components/StatTile";
import { formatBytes, formatPercent } from "../utils/format";

import "./HostHealth.scss";

/** Props for {@link HostHealth}. */
export interface HostHealthProps {
  /** CPU percentages behind the CPU sparkline, oldest first. */
  cpuHistory: number[];
  data: SystemData | undefined;
  /** Memory percentages behind the memory sparkline, oldest first. */
  memoryHistory: number[];
}

function fullest(disks: SystemDisk[]): SystemDisk | null {
  if (disks.length === 0) return null;
  return [...disks].sort((a, b) => b.percent - a.percent)[0];
}

function hottest(temperatures: SystemTemperature[]): SystemTemperature | null {
  if (temperatures.length === 0) return null;
  return [...temperatures].sort((a, b) => b.celsius - a.celsius)[0];
}

/** Anything above 90% is critical, above 75% is worth noticing. */
function usageTone(percent: number | null | undefined): StatTone {
  if (percent == null) return "normal";
  if (percent >= 90) return "critical";
  if (percent >= 75) return "warning";
  return "normal";
}

function thermalTone(celsius: number): StatTone {
  if (celsius >= 85) return "critical";
  if (celsius >= 70) return "warning";
  return "normal";
}

/** The five figures that say whether the host running the bench is healthy. */
export const HostHealth: React.FC<HostHealthProps> = ({ cpuHistory, data, memoryHistory }) => {
  const disk = fullest(data?.disks ?? []);
  const thermal = hottest(data?.temperatures ?? []);

  return (
    <div className="host-health">
      <StatTile
        label="CPU"
        value={formatPercent(data?.cpu_percent)}
        detail={data?.cpu_per_core.length ? `${data.cpu_per_core.length} cores` : "sampling"}
        percent={data?.cpu_percent}
        samples={cpuHistory}
        tone={usageTone(data?.cpu_percent)}
      />
      <StatTile
        label="Load average"
        value={data?.load_avg ? data.load_avg.map((one) => one.toFixed(2)).join("  ") : "-"}
        detail="1m · 5m · 15m"
      />
      <StatTile
        label="Memory"
        value={formatPercent(data?.memory.percent)}
        detail={`${formatBytes(data?.memory.used)} of ${formatBytes(data?.memory.total)}`}
        percent={data?.memory.percent}
        samples={memoryHistory}
        tone={usageTone(data?.memory.percent)}
      />
      <StatTile
        label="Disk"
        value={disk ? formatPercent(disk.percent) : "-"}
        detail={disk ? `${disk.mount} · ${formatBytes(disk.free)} free` : "no filesystems"}
        percent={disk?.percent}
        tone={usageTone(disk?.percent)}
      />
      <StatTile
        label="Hottest zone"
        value={thermal ? `${thermal.celsius.toFixed(1)} °C` : "-"}
        detail={thermal ? thermal.label : "no sensors"}
        tone={thermal ? thermalTone(thermal.celsius) : "normal"}
      />
    </div>
  );
};

export default HostHealth;
