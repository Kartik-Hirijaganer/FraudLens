/**
 * Summary: The wise button primitive. Three variants — primary (the lime CTA, the
 * brand's sole accent), secondary (sage), tertiary (outlined) — all sharing the
 * 24px (`xl`) radius and button-md type per DESIGN.md. Styling uses design tokens
 * only; the caller may append a className.
 *
 * Key classes:
 * - ButtonProps: props (variant + native button attributes).
 *
 * Key functions:
 * - Button: render a themed button element.
 *
 * Notes:
 * - primary uses `bg-primary` (Wise green) and is reserved for the main CTA — never
 *   as a success indicator (use Badge tone="positive" for status).
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cx } from "../../lib/cx";

export type ButtonVariant = "primary" | "secondary" | "tertiary";

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: "bg-primary text-on-primary",
  secondary: "bg-canvas-soft text-ink",
  tertiary: "border border-ink bg-canvas text-ink",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  type = "button",
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx("rounded-xl px-xl py-md text-button-md", VARIANT_CLASSES[variant], className)}
      {...rest}
    >
      {children}
    </button>
  );
}
