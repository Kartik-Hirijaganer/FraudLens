/**
 * Summary: The wise labelled select primitive, mirroring TextInput's chrome (12px `md`
 * radius, ink hairline border, body-md type per DESIGN.md) so dropdowns — status
 * filters, the model selector, the resolution-label picker — look native to the system.
 * A `useId`-generated id wires the label to the control for accessibility (jsx-a11y)
 * unless the caller supplies an explicit id.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Select: render a labelled select element.
 *
 * Notes:
 * - Styling uses design tokens only; the caller may append a className.
 */
import type { SelectHTMLAttributes } from "react";
import { useId } from "react";

import { cx } from "../../lib/cx";

interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  options: ReadonlyArray<SelectOption>;
}

export function Select({ label, id, className, options, ...rest }: SelectProps) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  return (
    <div className="gap-xs flex flex-col">
      <label htmlFor={selectId} className="text-body-sm text-body">
        {label}
      </label>
      <select
        id={selectId}
        className={cx(
          "rounded-md border border-ink bg-canvas px-lg py-md text-body-md text-ink",
          className,
        )}
        {...rest}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}
