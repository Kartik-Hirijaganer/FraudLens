/**
 * Summary: Accessible segmented control for small mutually exclusive filters and
 * modes. It replaces duplicated select/chip filter markup while keeping keyboard
 * navigation and labelled radio semantics.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - SegmentedControl: render a token-only radio group as segmented buttons.
 *
 * Notes:
 * - Values are strings so pages can pass API query values without local adapters.
 */
import { useId } from "react";

import { cx } from "../../lib/cx";

interface SegmentedOption {
  value: string;
  label: string;
}

interface SegmentedControlProps {
  options: ReadonlyArray<SegmentedOption>;
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  size?: "sm" | "md";
}

export function SegmentedControl({
  options,
  value,
  onChange,
  ariaLabel,
  size = "md",
}: SegmentedControlProps) {
  const groupId = useId();
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className="gap-xs bg-canvas-soft p-xs inline-flex flex-wrap rounded-xl"
    >
      {options.map((option) => {
        const checked = option.value === value;
        const id = `${groupId}-${option.value || "all"}`;
        return (
          <label
            key={option.value}
            htmlFor={id}
            className={cx(
              "rounded-lg text-body-sm font-semibold transition-colors",
              size === "sm" ? "px-md py-sm" : "px-lg py-md",
              checked ? "bg-canvas text-ink" : "text-body hover:text-ink",
            )}
          >
            <input
              id={id}
              type="radio"
              name={groupId}
              value={option.value}
              checked={checked}
              onChange={() => onChange(option.value)}
              className="sr-only"
            />
            {option.label}
          </label>
        );
      })}
    </div>
  );
}
