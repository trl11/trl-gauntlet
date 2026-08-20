import { faRotate, faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Spinner } from "@trl11/components/ui";
import { useState } from "react";

import { listInstruments, rescanInstruments, sendInstrumentCommand } from "@api/client";
import EmptyState from "@components/EmptyState";
import InstrumentPanel, { type InstrumentPreview } from "@components/InstrumentPanel";
import PageHeader from "@components/PageHeader";

import "./InstrumentsPage.scss";

/** How often the instrument snapshot is re-read. */
const POLL_MS = 2000;

/**
 * The image a command answered with, as a data URL, or null when it sent none.
 *
 * An instrument that answers with `image_base64` is showing the operator a
 * picture, whatever the command was called and whatever the instrument is.
 */
function imageFrom(result: Record<string, unknown>): string | null {
  const encoded = result.image_base64;
  if (typeof encoded !== "string" || encoded === "") return null;
  return `data:${result.suffix === ".jpg" ? "image/jpeg" : "image/png"};base64,${encoded}`;
}

/** Instrument availability and manual control. */
export const InstrumentsPage: React.FC = () => {
  const client = useQueryClient();
  const [failure, setFailure] = useState<{ message: string; name: string } | null>(null);
  // Kept apart from the mutation's own result so that running some other
  // command does not take the picture off the panel.
  const [shot, setShot] = useState<(InstrumentPreview & { name: string }) | null>(null);

  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: listInstruments,
    refetchInterval: POLL_MS,
  });

  const scan = useMutation({
    mutationFn: rescanInstruments,
    onSuccess: (result) => client.setQueryData(["instruments"], result),
  });

  const send = useMutation({
    mutationFn: (input: { args: Record<string, unknown>; command: string; name: string }) =>
      sendInstrumentCommand(input.name, input.command, input.args),
    onSuccess: (result, input) => {
      setFailure(null);
      const src = imageFrom(result.result);
      if (src !== null) setShot({ name: input.name, src });
      client.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: (error, input) => setFailure({ message: error.message, name: input.name }),
  });

  const rows = instruments.data?.instruments ?? [];

  return (
    <div className="instruments-page">
      <PageHeader
        title="Instruments"
        actions={
          <Button color="blue" disabled={scan.isPending} onClick={() => scan.mutate()}>
            <FontAwesomeIcon icon={faRotate} spin={scan.isPending} />
            Scan
          </Button>
        }
      />

      {instruments.isPending && <Spinner />}

      {instruments.isError && (
        <EmptyState
          icon={faTriangleExclamation}
          title="Could not read the instruments"
          message={instruments.error.message}
        />
      )}

      {instruments.isSuccess && rows.length === 0 && (
        <EmptyState
          title="No instruments detected"
          message="Gauntlet found no instruments attached. Connect one and scan to drive it from here."
        />
      )}

      <div className="instruments-page__list">
        {rows.map((instrument) => (
          <section key={instrument.name} className="instruments-page__card">
            <InstrumentPanel
              busy={send.isPending && send.variables?.name === instrument.name}
              error={failure?.name === instrument.name ? failure.message : null}
              instrument={instrument}
              onCommand={(command, args) => send.mutate({ args, command, name: instrument.name })}
              onDismiss={() => setShot(null)}
              preview={shot?.name === instrument.name ? shot : null}
            />
          </section>
        ))}
      </div>
    </div>
  );
};

export default InstrumentsPage;
