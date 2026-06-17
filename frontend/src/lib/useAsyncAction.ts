/**
 * Summary: The shared mutation-action hook the review + model-admin pages use so a
 * triggered action behaves identically everywhere (rule 5: no duplication). It runs the
 * action behind a `busy` guard (disables controls to prevent double-submits), shows a
 * PHI-free success toast (when a title is given) or routes a thrown value through
 * `notifyError`, and calls `onSuccess` (typically the page's reload) only on success.
 * Actions with bespoke toasts pass no title and toast inside the action themselves.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - useAsyncAction: run a mutation with a busy guard, success/error toast, and reload.
 *
 * Notes:
 * - `onSuccess` is invoked only when the action resolves; a failure leaves state untouched
 * and surfaces the error as a toast (no partial reload).
 */
import { useCallback, useState } from "react";

import { notify, notifyError } from "./toast";

interface AsyncAction {
  busy: boolean;
  run: (action: () => Promise<unknown>, successTitle?: string) => Promise<void>;
}

export function useAsyncAction(onSuccess: () => void): AsyncAction {
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async (action: () => Promise<unknown>, successTitle?: string): Promise<void> => {
      setBusy(true);
      try {
        await action();
        if (successTitle) {
          notify({ tone: "positive", title: successTitle });
        }
        onSuccess();
      } catch (caught) {
        notifyError(caught);
      } finally {
        setBusy(false);
      }
    },
    [onSuccess],
  );
  return { busy, run };
}
