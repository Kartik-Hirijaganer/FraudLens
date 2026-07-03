/**
 * Summary: The wise button primitive. Three variants — primary (the lime CTA, the
 * brand's sole accent), secondary (sage), tertiary (outlined) — and two sizes (md
 * default; sm for compact inline actions like a queue row's Review), all sharing the
 * 24px (`xl`) radius per DESIGN.md. Styling uses design tokens only; the caller may
 * append a className.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Button: render a themed button element.
 *
 * Notes:
 * - primary uses `bg-primary` (Wise green) and is reserved for the main CTA — never
 * as a success indicator (use Badge tone="positive" for status).
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cx } from "../../lib/cx";

type ButtonVariant = "primary" | "secondary" | "tertiary";
type ButtonSize = "md" | "sm";

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: "bg-primary text-on-primary",
  secondary: "bg-canvas-soft text-ink",
  tertiary: "border border-ink bg-canvas text-ink",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  md: "px-xl py-md text-button-md",
  sm: "px-lg py-sm text-body-sm",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  size = "md",
  type = "button",
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        "rounded-xl font-semibold",
        SIZE_CLASSES[size],
        VARIANT_CLASSES[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
