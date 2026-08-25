/**
 * Summary: Reusable machine-progress timeline for investigation and alert provenance. It renders
 * the deterministic single-writer path or the bounded four-agent fork/revision path, exposes each
 * row through an accessible Disclosure, and owns exactly one polite live region for status updates.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - AgentTimeline: render the workflow header, ordered execution graph, and expandable provenance.
 *
 * Notes:
 * - The human CaseStepper remains separate: this component reports machine execution only.
 * - Status always differs by glyph and text as well as semantic colour. A failed agent never
 *   promotes itself into a page-level error; only `run.failed` may do that.
 */
import { Badge } from "./ui/Badge";
import { Disclosure } from "./ui/Disclosure";
import { formatDurationMs, humanize } from "../lib/format";
import {
  investigationTimeline,
  type AgentTimelineRow,
  type AgentTimelineStatus,
  type InvestigationState,
} from "../lib/investigation";
import { agentGlyph, agentTone } from "../lib/risk";

const GLYPH_CLASSES: Record<AgentTimelineStatus, string> = {
  completed: "text-positive-deep",
  degraded: "text-warning-deep",
  failed: "text-negative-darkest",
  pending: "text-mute",
  running: "text-body",
  revision_requested: "text-warning-deep",
  skipped: "text-mute",
  awaiting: "text-body",
};

function statusLabel(row: AgentTimelineRow): string {
  if (row.status === "degraded" && row.agentRun?.errorCode) {
    return humanize(row.agentRun.errorCode);
  }
  return humanize(row.status);
}

function detailValue(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function AgentRunDetails({ row, graphVersion }: { row: AgentTimelineRow; graphVersion?: string }) {
  const run = row.agentRun;
  if (row.status === "failed") {
    return (
      <p className="text-body-sm text-negative-darkest">
        {humanize(run?.errorCode ?? "agent_failed")}
      </p>
    );
  }
  if (!run) {
    return (
      <div className="gap-xs flex flex-col">
        <p className="text-body-sm text-body">{row.purpose}</p>
        {row.status === "skipped" ? (
          <p className="text-body-sm text-mute">
            Skipped because the required upstream result was unavailable.
          </p>
        ) : null}
      </div>
    );
  }

  const evidenceSources = run.toolCalls.map((tool) => humanize(tool.name));
  return (
    <div className="gap-md flex flex-col">
      <div className="gap-xs flex flex-col">
        <h4 className="text-body-sm text-ink font-semibold">Purpose</h4>
        <p className="text-body-sm text-body">{row.purpose}</p>
      </div>

      <div className="gap-xs flex flex-col">
        <h4 className="text-body-sm text-ink font-semibold">Evidence consumed</h4>
        <p className="text-body-sm text-body">
          {evidenceSources.length > 0
            ? evidenceSources.join(", ")
            : "No governed tool evidence was recorded for this role."}
        </p>
      </div>

      <div className="gap-xs flex flex-col">
        <h4 className="text-body-sm text-ink font-semibold">Tool calls made</h4>
        {run.toolCalls.length > 0 ? (
          <ul className="gap-xs flex flex-col">
            {run.toolCalls.map((tool, index) => (
              <li key={tool.callId ?? `${tool.name}-${index}`} className="text-body-sm text-body">
                {humanize(tool.name)} · {humanize(tool.status)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-body-sm text-mute">None.</p>
        )}
      </div>

      {run.result !== null && run.result !== undefined ? (
        <div className="gap-xs flex flex-col">
          <h4 className="text-body-sm text-ink font-semibold">Structured result</h4>
          <pre className="bg-canvas p-md text-caption text-body overflow-x-auto whitespace-pre-wrap rounded-md">
            {detailValue(run.result)}
          </pre>
        </div>
      ) : null}

      <dl className="gap-sm grid grid-cols-1">
        <ProvenanceTerm label="Model" value={run.modelId} />
        <ProvenanceTerm
          label="Prompt"
          value={
            run.promptVersion
              ? `${run.promptVersion}${run.promptHash ? ` · ${run.promptHash.slice(0, 8)}` : ""}`
              : undefined
          }
        />
        <ProvenanceTerm label="Graph" value={graphVersion} />
        <ProvenanceTerm
          label="Attempt"
          value={`${run.attempt}${run.attempt > 1 ? " · retry" : ""}`}
        />
        <ProvenanceTerm
          label="Latency"
          value={run.latencyMs === undefined ? undefined : formatDurationMs(run.latencyMs)}
        />
        <ProvenanceTerm
          label="Tokens"
          value={run.totalTokens === undefined ? undefined : run.totalTokens.toLocaleString()}
        />
        <ProvenanceTerm label="Cost" value={run.costUsd ? `$${run.costUsd}` : undefined} />
      </dl>

      {row.status === "degraded" ? (
        <p className="bg-warning p-md text-body-sm text-warning-content rounded-md">
          A usable partial result was produced off the happy path. Downstream review continued with
          this limitation visible.
        </p>
      ) : null}
    </div>
  );
}

function ProvenanceTerm({ label, value }: { label: string; value?: string }) {
  return (
    <div className="gap-sm text-body-sm grid grid-cols-[auto_1fr]">
      <dt className="text-mute font-semibold">{label}</dt>
      <dd className="text-body break-all">{value ?? "—"}</dd>
    </div>
  );
}

function TimelineCard({ row, graphVersion }: { row: AgentTimelineRow; graphVersion?: string }) {
  return (
    <Disclosure
      summary={
        <span className="gap-sm flex items-center">
          <span
            aria-hidden="true"
            className={`${GLYPH_CLASSES[row.status]} text-body-md w-xl shrink-0 text-center font-semibold`}
          >
            {agentGlyph(row.status)}
          </span>
          <span className="gap-xs flex min-w-0 grow flex-col">
            <span className="text-body-sm text-ink font-semibold">{row.label}</span>
            <span className="text-caption text-mute">{row.purpose}</span>
          </span>
          <Badge tone={agentTone(row.status)} className="shrink-0">
            {statusLabel(row)}
          </Badge>
        </span>
      }
    >
      <AgentRunDetails row={row} graphVersion={graphVersion} />
    </Disclosure>
  );
}

function TimelineItem({ row, graphVersion }: { row: AgentTimelineRow; graphVersion?: string }) {
  if (!row.children) {
    return (
      <li>
        <TimelineCard row={row} graphVersion={graphVersion} />
      </li>
    );
  }
  return (
    <li className="gap-sm bg-canvas-soft p-md flex flex-col rounded-lg">
      <div className="gap-sm flex items-center">
        <span
          aria-hidden="true"
          className={`${GLYPH_CLASSES[row.status]} text-body-md w-xl shrink-0 text-center font-semibold`}
        >
          {agentGlyph(row.status)}
        </span>
        <span className="text-body-sm text-ink grow font-semibold">{row.label}</span>
        <Badge tone={agentTone(row.status)}>{statusLabel(row)}</Badge>
      </div>
      <ol className="gap-sm ml-xl flex flex-col" aria-label="Parallel agent executions">
        {row.children.map((child) => (
          <TimelineItem key={child.id} row={child} graphVersion={graphVersion} />
        ))}
      </ol>
    </li>
  );
}

interface AgentTimelineProps {
  state: InvestigationState;
  title?: string;
}

export function AgentTimeline({ state, title = "Execution timeline" }: AgentTimelineProps) {
  const rows = investigationTimeline(state);
  const modeLabel = state.workflowMode === "multi_agent" ? "4-agent review" : "Single-writer";
  const flatRows = rows.flatMap((row) => row.children ?? [row]);
  const completedCount = flatRows.filter((row) => row.status === "completed").length;

  return (
    <section className="gap-md flex flex-col" aria-label={title}>
      <div className="gap-sm flex flex-wrap items-center justify-between">
        <h2 className="text-display-xs text-ink">{title}</h2>
        <div className="gap-xs flex flex-wrap">
          <Badge tone="neutral">{modeLabel}</Badge>
          {state.recorded ? <Badge tone="neutral">Recorded</Badge> : null}
        </div>
      </div>
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {modeLabel}. {completedCount} of {flatRows.length} machine steps complete.
      </p>
      <ol className="gap-sm flex flex-col">
        {rows.map((row) => (
          <TimelineItem key={row.id} row={row} graphVersion={state.graphVersion} />
        ))}
      </ol>
    </section>
  );
}
