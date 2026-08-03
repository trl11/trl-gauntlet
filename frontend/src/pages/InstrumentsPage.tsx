import { faRotate, faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Spinner } from "@trl11/components/ui";
import { useState } from "react";

import { listInstruments, scanInstruments, sendInstrumentCommand } from "@api/client";
import EmptyState from "@components/EmptyState";
import InstrumentPanel from "@components/InstrumentPanel";
import PageHeader from "@components/PageHeader";

import "./InstrumentsPage.scss";

/** How often the instrument snapshot is re-read. */
const POLL_MS = 2000;

/** Instrument availability and manual control. */
export const InstrumentsPage: React.FC = () => {
  const client = useQueryClient();
  const [failure, setFailure] = useState<{ message: string; name: string } | null>(null);

  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: listInstruments,
    refetchInterval: POLL_MS,
  });

  const scan = useMutation({
    mutationFn: scanInstruments,
    onSuccess: (result) => client.setQueryData(["instruments"], result),
  });

  const send = useMutation({
    mutationFn: (input: { args: Record<string, unknown>; command: string; name: string }) =>
      sendInstrumentCommand(input.name, input.command, input.args),
    onSuccess: () => {
      setFailure(null);
      client.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: (error, input) => setFailure({ message: error.message, name: input.name }),
  });

  const rows = instruments.data?.instruments ?? [];

  return (
    <div className="instruments-page">
      <PageHeader
        title="Instruments"
        subtitle="What Gauntlet can drive on a suite's behalf"
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
          title="No instruments registered"
          message="Gauntlet is holding no instruments. Register one to drive it from here."
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
            />
          </section>
        ))}
      </div>
    </div>
  );
};

export default InstrumentsPage;
