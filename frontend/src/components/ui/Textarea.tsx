/**
 * Summary: The wise multi-line text input, mirroring TextInput's chrome (12px `md`
 * radius, ink hairline border, body-md type per DESIGN.md) for the notes / rejection
 * reason / SAR edit fields in the review workflow. A `useId`-generated id wires the label
 * to the control for accessibility unless the caller supplies an explicit id.
 *
 * Key classes:
 * - TextareaProps: props (required label + native textarea attributes).
 *
 * Key functions:
 * - Textarea: render a labelled multi-line text input.
 *
 * Notes:
 * - Styling uses design tokens only; the caller may append a className (e.g. row sizing).
 */
import type { TextareaHTMLAttributes } from "react";
import { useId } from "react";

import { cx } from "../../lib/cx";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string;
}

export function Textarea({ label, id, className, ...rest }: TextareaProps) {
  const generatedId = useId();
  const areaId = id ?? generatedId;
  return (
    <div className="gap-xs flex flex-col">
      <label htmlFor={areaId} className="text-body-sm text-body">
        {label}
      </label>
      <textarea
        id={areaId}
        className={cx(
          "rounded-md border border-ink bg-canvas px-lg py-md text-body-md text-ink",
          className,
        )}
        {...rest}
      />
    </div>
  );
}
