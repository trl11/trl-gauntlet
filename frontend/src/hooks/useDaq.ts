import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { getUpsets } from "@api/client";

/** How often the record of a run in flight is refreshed. */
const LIVE_POLL_MS = 2000;

/** How long the DAQ view flashes after an event arrives. */
const FLASH_MS = 1500;

/**
 * What a run's DAQ monitor is watching and has recorded, and whether an event
 * has just arrived.
 *
 * `eventCount` is how many `upset` events the caller's event stream has seen;
 * each new one refreshes the record and starts the flash.
 */
export function useDaq(runId: string, live: boolean, eventCount: number) {
  const queryClient = useQueryClient();
  const upsets = useQuery({
    queryKey: ["upsets", runId],
    queryFn: () => getUpsets(runId),
    refetchInterval: live ? LIVE_POLL_MS : false,
  });
  const [flashing, setFlashing] = useState(false);
  useEffect(() => {
    if (eventCount === 0) return;
    queryClient.invalidateQueries({ queryKey: ["upsets", runId] });
    setFlashing(true);
    const timer = setTimeout(() => setFlashing(false), FLASH_MS);
    return () => clearTimeout(timer);
  }, [queryClient, runId, eventCount]);
  return { flashing, upsets };
}

export default useDaq;
