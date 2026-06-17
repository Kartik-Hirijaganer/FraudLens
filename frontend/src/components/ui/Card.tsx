/**
 * Summary: The wise content card — a white (`canvas`) surface with the signature
 * 24px (`xl`) radius and `xl` interior padding, designed to sit on the sage
 * (`canvas-soft`) page background where the surface contrast carries elevation.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Card: render a themed card container.
 *
 * Notes:
 * - No border/shadow by design; contrast against the sage canvas is the elevation.
 */
import type { HTMLAttributes, ReactNode } from "react";

import { cx } from "../../lib/cx";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

export function Card({ className, children, ...rest }: CardProps) {
  return (
    <div className={cx("rounded-xl bg-canvas p-xl text-ink", className)} {...rest}>
      {children}
    </div>
  );
}
