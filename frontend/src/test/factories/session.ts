// Test-only synthetic login personas standing in for the backend public projection.
import type { DemoRole, UserRole } from "../../lib/session";

// Synthetic login personas standing in for the backend's public portfolio-demo projection.
// Test-only: production code receives these from the API, never from a TypeScript constant.
const PERSONA_PRESETS: Record<UserRole, Pick<DemoRole, "name" | "tag" | "accent" | "analyst">> = {
  analyst: {
    name: "Fraud Analyst",
    tag: "Queue",
    accent: "green",
    analyst: { name: "Alex Rivera", initials: "AR" },
  },
  reviewer: {
    name: "Reviewer",
    tag: "Approve",
    accent: "cyan",
    analyst: { name: "Morgan Diaz", initials: "MD" },
  },
  admin: {
    name: "Compliance Admin",
    tag: "Model",
    accent: "amber",
    analyst: { name: "Priya Shah", initials: "PS" },
  },
  auditor: {
    name: "Auditor",
    tag: "Read-only",
    accent: "slate",
    analyst: { name: "Jordan Lee", initials: "JL" },
  },
};

export const TEST_DEMO_AGENCY_ID = "00000000-0000-4000-8000-00000000d3m0";
// Test-owned mail domain. Deliberately NOT the configured personas' domain: the frontend
// suite must prove the picker renders whatever the backend projection returns, so it can
// never assert against a value copied out of config/portfolio-demo.yaml.
export const TEST_EMAIL_DOMAIN = "tenant.test";
const TEST_DEMO_PASSWORD = "synthetic-test-password";

export function demoPersona(role: UserRole, overrides: Partial<DemoRole> = {}): DemoRole {
  return {
    id: role,
    role,
    email: `${role}@${TEST_EMAIL_DOMAIN}`,
    demoPassword: TEST_DEMO_PASSWORD,
    agencyId: TEST_DEMO_AGENCY_ID,
    ...PERSONA_PRESETS[role],
    ...overrides,
  };
}

export function demoPersonas(): readonly DemoRole[] {
  return (["analyst", "reviewer", "admin", "auditor"] as const).map((role) => demoPersona(role));
}
