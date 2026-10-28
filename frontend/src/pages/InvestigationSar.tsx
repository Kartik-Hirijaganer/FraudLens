/**
 * Summary: Inline investigation SAR preparation using authoritative case permissions and versions.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - InvestigationSar: load case editing access and reuse the case narrative editor.
 *
 * Notes:
 * - Loading failures retain streamed evidence; saving uses the existing versioned case command.
 */
import { useCallback } from "react";
import { SarStream } from "../components/SarStream";
import { Button } from "../components/ui/Button";
import type { ApiClient, SarDraftView } from "../lib/api";
import type { InvestigationState } from "../lib/investigation";
import { useAsync } from "../lib/useAsync";
import { CasePreparation } from "./CasePreparation";

export function InvestigationSar({
  alertId,
  state,
  client,
  dirty,
  onDirtyChange,
  onSaved,
}: {
  alertId: string;
  state: InvestigationState;
  client: ApiClient;
  dirty: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onSaved: (draft: SarDraftView) => void;
}) {
  const load = useCallback(() => client.getAlert(alertId), [client, alertId]);
  const access = useAsync(load, [client, alertId]);
  return (
    <div className="gap-md flex flex-col">
      {access.data ? (
        <CasePreparation
          key={`${alertId}:${access.data.alert.caseVersion}`}
          detail={access.data}
          client={client}
          reload={access.reload}
          narrativeOnly
          onDirtyChange={onDirtyChange}
          onDraftSaved={onSaved}
        />
      ) : (
        <SarStream
          text={state.sarText}
          streaming={false}
          failed={state.sarStatus === "failed"}
          template={state.sarModelId === "template"}
        />
      )}
      {access.loading ? (
        <p role="status" className="text-body-sm text-mute">
          Loading the saved SAR…
        </p>
      ) : null}
      {access.error ? (
        <p role="status" className="text-body-sm text-warning-deep">
          The saved SAR could not be refreshed. Your narrative is kept; reload the SAR to try again.
        </p>
      ) : null}
      <Button
        variant="tertiary"
        size="sm"
        className="self-start"
        disabled={dirty || access.loading}
        onClick={access.reload}
      >
        Reload SAR
      </Button>
      {dirty ? (
        <p className="text-body-sm text-body">Save or cancel your changes before continuing.</p>
      ) : null}
    </div>
  );
}
