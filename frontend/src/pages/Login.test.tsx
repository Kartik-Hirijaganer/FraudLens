import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", () => ({
  fetchCurrentUser: vi.fn(),
}));

vi.mock("../lib/supabase", () => ({
  signInWithPassword: vi.fn(),
  signOutSupabase: vi.fn(),
}));

import {
  Login,
  isDemoBypassEnabled,
  isDemoPickerEnabled,
  isLiveDemoAuthEnabled,
  type LoginEnv,
} from "./Login";
import { fetchCurrentUser } from "../lib/api";
import { getSession, signOut } from "../lib/session";
import {
  TEST_DEMO_AGENCY_ID,
  TEST_EMAIL_DOMAIN,
  demoPersona,
  demoPersonas,
} from "../test/factories";
import { signInWithPassword } from "../lib/supabase";

// The personas the backend projection hands the picker; the component owns no persona data.
const PERSONAS = demoPersonas();

const LOCAL_DEMO_ENV: LoginEnv = {
  DEV: true,
  VITE_AUTH_DEV_BYPASS: "true",
  VITE_DEMO_AUTH_ENABLED: "false",
};

const LIVE_DEMO_ENV: LoginEnv = {
  DEV: true,
  VITE_AUTH_DEV_BYPASS: "false",
  VITE_DEMO_AUTH_ENABLED: "true",
};

const PORTFOLIO_DEMO_ENV: LoginEnv = {
  DEV: false,
  VITE_AUTH_DEV_BYPASS: "false",
  VITE_DEMO_AUTH_ENABLED: "true",
};

const HIDDEN_DEMO_ENV: LoginEnv = {
  DEV: true,
  VITE_AUTH_DEV_BYPASS: "false",
  VITE_DEMO_AUTH_ENABLED: "false",
};

// Both gates on: the picker renders and the tokenless bypass is available, so a picked persona
// signs in without Supabase.
const FULL_DEMO_ENV: LoginEnv = {
  DEV: true,
  VITE_AUTH_DEV_BYPASS: "true",
  VITE_DEMO_AUTH_ENABLED: "true",
};

afterEach(() => {
  signOut();
  vi.mocked(fetchCurrentUser).mockReset();
  vi.mocked(signInWithPassword).mockReset();
});

async function openRolePicker(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByRole("button", { name: "Demo · sign in as" }));
}

describe("Login", () => {
  it("renders the sign-in heading and the empty credential fields", () => {
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Sign in to your account" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Work email")).toHaveValue("");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("auto-fills the email and password when a demo role is chosen", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    const role = demoPersona("reviewer");

    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(role.name) }));

    expect(screen.getByLabelText("Work email")).toHaveValue(role.email);
    expect(screen.getByLabelText("Password")).toHaveValue(role.demoPassword);
  });

  it("lists exactly the supplied personas once the picker is open, then closes on select", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    await openRolePicker(user);
    expect(screen.getAllByRole("option")).toHaveLength(PERSONAS.length);
    for (const role of PERSONAS) {
      expect(screen.getByRole("option", { name: new RegExp(role.name) })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("option", { name: new RegExp(PERSONAS[0].name) }));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("renders no options when the backend supplied no personas", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} />);
    await openRolePicker(user);
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });

  it("says the projection is loading and offers nothing to pick yet", () => {
    render(<Login env={LOCAL_DEMO_ENV} personasStatus="loading" />);
    const trigger = screen.getByRole("button", { name: "Demo · sign in as" });
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveTextContent("Loading demo personas…");
    expect(screen.getByText("loading personas…")).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("says so plainly when the projection is unavailable, rather than showing an empty list", () => {
    render(<Login env={LOCAL_DEMO_ENV} personasStatus="failed" />);
    const trigger = screen.getByRole("button", { name: "Demo · sign in as" });
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveTextContent("Demo personas unavailable — sign in above");
    expect(screen.getByText("personas unavailable")).toBeInTheDocument();
  });

  it("invites a selection once the projection is ready", () => {
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} personasStatus="ready" />);
    const trigger = screen.getByRole("button", { name: "Demo · sign in as" });
    expect(trigger).toBeEnabled();
    expect(trigger).toHaveTextContent("Choose a role to auto-fill…");
    expect(screen.getByText("credentials auto-filled")).toBeInTheDocument();
  });

  it.each(["loading", "failed", "ready"] as const)(
    "keeps ordinary email-password sign-in usable while the projection is %s",
    async (personasStatus) => {
      // A missing or slow demo projection must never block a real user from signing in.
      vi.mocked(signInWithPassword).mockResolvedValue("real-token");
      vi.mocked(fetchCurrentUser).mockResolvedValue({
        email: "real@agency.gov",
        displayName: "Real User",
        role: "analyst",
        agencyId: "agency-1",
      });
      const user = userEvent.setup();
      const view = render(<Login env={LIVE_DEMO_ENV} personasStatus={personasStatus} />);

      await user.type(screen.getByLabelText("Work email"), "real@agency.gov");
      await user.type(screen.getByLabelText("Password"), "real-password");
      const submit = screen.getByRole("button", { name: /Sign in/ });
      expect(submit).toBeEnabled();
      await user.click(submit);

      await waitFor(() => expect(getSession()).toMatchObject({ email: "real@agency.gov" }));
      view.unmount();
      signOut();
    },
  );

  it("offers the same personas in a production portfolio build", async () => {
    const user = userEvent.setup();
    render(<Login env={PORTFOLIO_DEMO_ENV} personas={PERSONAS} />);

    await openRolePicker(user);

    expect(screen.getAllByRole("option")).toHaveLength(PERSONAS.length);
    expect(screen.getByRole("option", { name: new RegExp(PERSONAS[0].email) })).toBeInTheDocument();
  });

  it("keeps the dev bypass tokenless and persists the configured agency", async () => {
    const user = userEvent.setup();
    render(<Login env={FULL_DEMO_ENV} personas={PERSONAS} />);
    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(PERSONAS[0].email) }));
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    // A tokenless bypass session: no Supabase call, demo role set, configured agency persisted.
    expect(signInWithPassword).not.toHaveBeenCalled();
    expect(getSession()).toMatchObject({
      email: PERSONAS[0].email,
      demoRole: PERSONAS[0].role,
      agencyId: TEST_DEMO_AGENCY_ID,
    });
  });

  it("closes the picker on Escape", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    await openRolePicker(user);
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("toggles password visibility with the Show/Hide control", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");

    await user.click(screen.getByRole("button", { name: "Show" }));
    expect(password).toHaveAttribute("type", "text");

    await user.click(screen.getByRole("button", { name: "Hide" }));
    expect(password).toHaveAttribute("type", "password");
  });

  it("keeps the submit button disabled until both fields are filled", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    const submit = screen.getByRole("button", { name: /Sign in/ });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText("Work email"), `analyst@${TEST_EMAIL_DOMAIN}`);
    await user.type(screen.getByLabelText("Password"), "any-non-empty-value");
    expect(submit).toBeEnabled();
  });

  it("does not start a session when the form is submitted with empty fields", () => {
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    fireEvent.submit(screen.getByRole("form", { name: "Sign in" }));
    expect(getSession()).toBeNull();
  });

  it("surfaces a notice from the Forgot? control without starting a session", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    await user.click(screen.getByRole("button", { name: "Forgot?" }));
    expect(getSession()).toBeNull();
  });

  it("starts a session on submit", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(PERSONAS[0].name) }));
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    expect(getSession()).toMatchObject({ email: PERSONAS[0].email, role: PERSONAS[0].role });
  });

  it("signs in with Supabase and stores the server-returned role and token", async () => {
    vi.mocked(signInWithPassword).mockResolvedValue("access-token");
    vi.mocked(fetchCurrentUser).mockResolvedValue({
      email: "reviewer@example.test",
      displayName: "Live Reviewer",
      role: "reviewer",
      agencyId: "agency-1",
    });
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);

    await user.type(screen.getByLabelText("Work email"), "reviewer@example.test");
    await user.type(screen.getByLabelText("Password"), "correct-password");
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    await waitFor(() =>
      expect(getSession()).toMatchObject({
        email: "reviewer@example.test",
        role: "reviewer",
        accessToken: "access-token",
        agencyId: "agency-1",
        // The display identity for a live user comes from the verified /me response.
        analyst: { name: "Live Reviewer", initials: "LR" },
      }),
    );
    expect(signInWithPassword).toHaveBeenCalledWith("reviewer@example.test", "correct-password");
    expect(fetchCurrentUser).toHaveBeenCalledWith("access-token");
  });

  it("keeps the bypass dev-only while allowing explicit live demo auth in production", () => {
    expect(
      isDemoPickerEnabled({
        DEV: false,
        VITE_AUTH_DEV_BYPASS: "true",
        VITE_DEMO_AUTH_ENABLED: "false",
      }),
    ).toBe(false);
    expect(
      isDemoPickerEnabled({
        DEV: true,
        VITE_AUTH_DEV_BYPASS: "false",
        VITE_DEMO_AUTH_ENABLED: "false",
      }),
    ).toBe(false);
    expect(
      isDemoPickerEnabled({
        DEV: false,
        VITE_AUTH_DEV_BYPASS: "false",
        VITE_DEMO_AUTH_ENABLED: "true",
      }),
    ).toBe(true);
    expect(
      isDemoPickerEnabled({
        DEV: true,
        VITE_AUTH_DEV_BYPASS: "false",
        VITE_DEMO_AUTH_ENABLED: "true",
      }),
    ).toBe(true);
    expect(isDemoBypassEnabled({ DEV: true, VITE_AUTH_DEV_BYPASS: "false" })).toBe(false);
    expect(isDemoBypassEnabled({ DEV: false, VITE_AUTH_DEV_BYPASS: "true" })).toBe(false);
    expect(isLiveDemoAuthEnabled({ VITE_DEMO_AUTH_ENABLED: "true" })).toBe(true);
    expect(isLiveDemoAuthEnabled({ VITE_DEMO_AUTH_ENABLED: "false" })).toBe(false);
  });

  it("hides demo identities when live demo auth is not explicitly enabled", () => {
    render(<Login env={HIDDEN_DEMO_ENV} personas={PERSONAS} />);
    expect(screen.queryByText("Demo · sign in as")).not.toBeInTheDocument();
  });

  it("uses real Supabase auth for a live demo persona", async () => {
    vi.mocked(signInWithPassword).mockResolvedValue("demo-access-token");
    const reviewer = demoPersona("reviewer");
    vi.mocked(fetchCurrentUser).mockResolvedValue({
      email: reviewer.email,
      displayName: reviewer.analyst.name,
      role: reviewer.role,
      agencyId: "agency-1",
    });
    const user = userEvent.setup();
    render(<Login env={LIVE_DEMO_ENV} personas={PERSONAS} />);

    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(reviewer.name) }));
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    await waitFor(() =>
      expect(getSession()).toMatchObject({
        email: reviewer.email,
        role: reviewer.role,
        accessToken: "demo-access-token",
      }),
    );
    expect(signInWithPassword).toHaveBeenCalledWith(reviewer.email, reviewer.demoPassword);
    expect(fetchCurrentUser).toHaveBeenCalledWith("demo-access-token");
  });

  it("persists the session to localStorage only when 'keep me signed in' is checked", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(PERSONAS[0].name) }));
    await user.click(screen.getByLabelText("Keep me signed in on this device"));
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    expect(window.localStorage.getItem("fraudlens.session")).not.toBeNull();
    expect(window.sessionStorage.getItem("fraudlens.session")).toBeNull();
  });

  it("renders an animated brand motif (drawing lines, pulse-nodes, panning grid, sweep)", () => {
    const { container } = render(<Login env={LOCAL_DEMO_ENV} personas={PERSONAS} />);
    expect(container.querySelectorAll(".motion-safe\\:animate-draw").length).toBe(2);
    expect(container.querySelectorAll(".motion-safe\\:animate-node-pulse").length).toBe(5);
    expect(container.querySelectorAll(".motion-safe\\:animate-grid-pan").length).toBe(2);
    expect(container.querySelector(".motion-safe\\:animate-sheen")).toBeInTheDocument();
  });
});
