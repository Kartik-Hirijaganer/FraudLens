/**
 * Summary: The sample FraudLens page, demonstrating the wise design system end to
 * end: a sage hero band with a weight-900 display headline, a white card holding a
 * labelled input and the lime primary CTA, and a status badge. The CTA calls the
 * typed API client and reflects the API-surface health, tracing the full
 * UI -> API path on the walking skeleton.
 *
 * Key classes:
 * - AppProps: props (an injectable health fetcher for tests).
 *
 * Key functions:
 * - App: render the sample page.
 *
 * Notes:
 * - Surfaces cycle sage canvas -> white card; the lime CTA is the only use of the
 *   brand accent. Status uses the semantic positive/negative badge palette.
 */
import { useState } from "react";

import { Badge } from "./components/ui/Badge";
import { Button } from "./components/ui/Button";
import { Card } from "./components/ui/Card";
import { TextInput } from "./components/ui/TextInput";
import { fetchApiHealth } from "./lib/api";
import { config } from "./lib/config";

export interface AppProps {
  healthFetcher?: typeof fetchApiHealth;
}

export function App({ healthFetcher = fetchApiHealth }: AppProps) {
  const [status, setStatus] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  async function checkHealth(): Promise<void> {
    try {
      const health = await healthFetcher();
      setStatus(health.status);
      setFailed(false);
    } catch {
      setStatus(null);
      setFailed(true);
    }
  }

  return (
    <main className="max-w-container gap-2xl px-xl py-3xl mx-auto flex flex-col">
      <header className="gap-md bg-canvas-soft p-3xl flex flex-col rounded-xl">
        <h1 className="font-display text-display-xl text-ink">FraudLens</h1>
        <p className="text-body-lg text-body">
          AML fraud investigation — risk scoring, regulatory RAG, and SAR drafting.
        </p>
      </header>

      <Card className="gap-lg flex flex-col">
        <h2 className="text-display-xs text-ink">API status</h2>
        <TextInput label="Agency ID" name="agencyId" placeholder="acme" />
        <div className="gap-md flex items-center">
          <Button onClick={() => void checkHealth()}>Check API health</Button>
          {status ? <Badge tone="positive">{status}</Badge> : null}
          {failed ? <Badge tone="negative">unavailable</Badge> : null}
        </div>
      </Card>

      <footer className="text-caption text-mute">FraudLens v{config.appVersion}</footer>
    </main>
  );
}
