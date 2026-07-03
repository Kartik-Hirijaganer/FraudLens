/**
 * Summary: The model-version selector (plan §5.4 `modelOverride`, §16 Phase 11
 * ModelSelector). It lets an analyst score an investigation with a specific registered
 * model version instead of the active/canary routing — the first option is the active
 * model (no override), and each registry version is listed with its lifecycle status.
 * Selecting the active option clears the override (passes `undefined`).
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - ModelSelector: render the model-override dropdown.
 *
 * Notes:
 * - Wraps the `Select` primitive; the empty value maps to "use the active/canary routing".
 */
import type { ModelVersionResponse } from "../lib/api";
import { formatModelVersion } from "../lib/format";
import { Select } from "./ui/Select";

interface ModelSelectorProps {
  versions: ModelVersionResponse[];
  activeLabel: string | null;
  value?: string;
  onChange: (label: string | undefined) => void;
  disabled?: boolean;
}

export function ModelSelector({
  versions,
  activeLabel,
  value,
  onChange,
  disabled,
}: ModelSelectorProps) {
  // Display the model version the SAME way every page does (formatModelVersion drops the
  // internal "-fixture" tag) so the selector and the Dashboard never show different labels
  // for the same model; the submitted `modelOverride` value stays the raw registry label.
  const activeText = activeLabel
    ? `Active model — ${formatModelVersion(activeLabel)}`
    : "Active model (default)";
  const options = [
    { value: "", label: activeText },
    ...versions.map((version) => ({
      value: version.versionLabel,
      label: `${formatModelVersion(version.versionLabel)} (${version.status})`,
    })),
  ];
  return (
    <Select
      label="Score with model"
      options={options}
      value={value ?? ""}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value || undefined)}
    />
  );
}
