/**
 * Summary: The wise text input with an associated label. Uses the 12px (`md`)
 * radius, ink hairline border, and body-md type per DESIGN.md. A useId-generated
 * id wires the label to the input for accessibility (jsx-a11y), unless the caller
 * supplies an explicit id.
 *
 * Key classes:
 * - TextInputProps: props (required label + native input attributes).
 *
 * Key functions:
 * - TextInput: render a labelled text input.
 *
 * Notes:
 * - The label/input association keeps the field screen-reader accessible.
 */
import type { InputHTMLAttributes } from "react";
import { useId } from "react";

import { cx } from "../../lib/cx";

export interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
}

export function TextInput({ label, id, className, ...rest }: TextInputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  return (
    <div className="gap-xs flex flex-col">
      <label htmlFor={inputId} className="text-body-sm text-body">
        {label}
      </label>
      <input
        id={inputId}
        className={cx(
          "rounded-md border border-ink bg-canvas px-lg py-md text-body-md text-ink",
          className,
        )}
        {...rest}
      />
    </div>
  );
}
