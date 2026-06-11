# Test fixtures

**Synthetic data only.** Every fixture in this directory is fabricated for tests.

- **No real PHI** — no real patient names, SSNs, DOBs, diagnoses, addresses, or any
  protected health information, ever (FraudLens / Golden Rules).
- **No secrets** — no real credentials, tokens, or keys. Secrets come from Infisical at
  runtime and are never committed (Golden Rule 2).
- Tenant ids (`agency_id`) are obviously-fake placeholders (e.g. `acme`, `dev-agency`).

If a future test needs realistic-looking data, generate it synthetically (e.g. Faker
with a fixed seed) — never copy from a real dataset.
