/**
 * Summary: Evidence panels shared by alert review: persisted risk/SHAP evidence and a compact,
 * synthetic-only disclosure of the exact allowlisted fields supplied to SAR drafting.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - AlertRiskEvidence: render the persisted score and SHAP drivers for the originating run.
 * - ModelInputDisclosure: render model-egress aliases, facts, drivers, and citation identifiers.
 *
 * Notes:
 * - `modelInput` is produced by the backend's fail-closed egress projector; raw records and
 *   credentials are never accepted by this API contract.
 */
import { FraudGauge } from "../components/FraudGauge";
import { ShapBarChart } from "../components/ShapBarChart";
import { Disclosure } from "../components/ui/Disclosure";
import type { InvestigationSnapshot, SarModelInputView } from "../lib/api";

interface AlertRiskEvidenceProps {
  investigation: InvestigationSnapshot;
}

export function AlertRiskEvidence({ investigation }: AlertRiskEvidenceProps) {
  const value = investigation.riskScore ?? investigation.fraudProbability;
  const label = investigation.riskScore !== null ? "risk score" : "fraud probability";
  return (
    <div className="gap-xl flex flex-col lg:flex-row lg:items-start">
      {value === null ? (
        <p className="text-body-sm text-body">No persisted risk score is available.</p>
      ) : (
        <FraudGauge value={value} band={investigation.riskBand ?? ""} label={label} />
      )}
      <div className="gap-md flex min-w-0 grow flex-col">
        <h3 className="text-body-md text-ink font-semibold">Top model drivers</h3>
        <ShapBarChart features={investigation.topFeatures} />
      </div>
    </div>
  );
}

interface ModelInputDisclosureProps {
  modelInput: SarModelInputView | null;
}

function compactJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function ModelInputDisclosure({ modelInput }: ModelInputDisclosureProps) {
  const fields: Array<{ label: string; value: unknown }> = modelInput
    ? [
        { label: "Verified facts", value: modelInput.transaction },
        { label: "Aggregates", value: modelInput.aggregates },
        { label: "Rule findings", value: modelInput.ruleHits },
        { label: "SHAP drivers", value: modelInput.shapDrivers },
        { label: "Unknowns", value: modelInput.unknowns },
      ]
    : [];
  return (
    <Disclosure
      summary={
        <span className="gap-xxs flex flex-col">
          <span className="text-body-sm text-ink font-semibold">What the model saw</span>
          <span className="text-caption text-body">Synthetic-only, allowlisted drafting input</span>
        </span>
      }
    >
      {modelInput ? (
        <dl className="gap-md grid grid-cols-1 sm:grid-cols-2">
          <div>
            <dt className="text-caption text-body">Aliases</dt>
            <dd className="text-body-sm text-ink">
              {modelInput.caseAlias} · {modelInput.subjectAlias} · {modelInput.counterpartyAlias}
            </dd>
          </div>
          <div>
            <dt className="text-caption text-body">Citation IDs</dt>
            <dd className="text-body-sm text-ink">
              {modelInput.regulations
                .map((item) => item.citationId)
                .filter(Boolean)
                .join(", ") || "None"}
            </dd>
          </div>
          {fields.map(({ label, value }) => (
            <div key={label} className="min-w-0 sm:col-span-2">
              <dt className="text-caption text-body">{label}</dt>
              <dd>
                <pre className="bg-canvas p-md text-caption text-body overflow-x-auto rounded-lg">
                  {compactJson(value)}
                </pre>
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-body-sm text-body">
          The exact allowlisted projection is unavailable for this older draft.
        </p>
      )}
    </Disclosure>
  );
}
