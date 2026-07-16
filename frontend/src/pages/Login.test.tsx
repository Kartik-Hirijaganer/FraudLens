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
import { DEMO_AGENCIES, DEMO_ROLES, getSession, signOut } from "../lib/session";
import { signInWithPassword } from "../lib/supabase";

// Personas offered by the tokenless dev bypass (Agency One only).
const BYPASS_PERSONAS = DEMO_ROLES.filter((role) => !role.requiresLiveAuth);
// The Agency Two analyst — only offered through real Supabase auth (an agency-bound JWT).
const LIVE_ONLY_PERSONA = DEMO_ROLES.find((role) => role.requiresLiveAuth);

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

const HIDDEN_DEMO_ENV: LoginEnv = {
  DEV: true,
  VITE_AUTH_DEV_BYPASS: "false",
  VITE_DEMO_AUTH_ENABLED: "false",
};

// Both gates on: the picker shows Agency Two AND the tokenless bypass is available, so the
// `requiresLiveAuth` guard (not the gate) must route Agency Two through Supabase.
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
    render(<Login env={LOCAL_DEMO_ENV} />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Sign in to your account" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Work email")).toHaveValue("");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("auto-fills the email and password when a demo role is chosen", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} />);
    const role = DEMO_ROLES[1];

    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(role.name) }));

    expect(screen.getByLabelText("Work email")).toHaveValue(role.email);
    expect(screen.getByLabelText("Password")).toHaveValue(role.demoPassword);
  });

  it("lists every dev-bypass persona once the picker is open, then closes on select", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} />);
    await openRolePicker(user);
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(BYPASS_PERSONAS.length);
    for (const role of BYPASS_PERSONAS) {
      expect(screen.getByRole("option", { name: new RegExp(role.name) })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("option", { name: new RegExp(DEMO_ROLES[0].name) }));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("offers the Agency Two persona only when live demo auth is enabled", async () => {
    expect(LIVE_ONLY_PERSONA).toBeDefined();
    const name = new RegExp(LIVE_ONLY_PERSONA!.name);
    const user = userEvent.setup();

    const local = render(<Login env={LOCAL_DEMO_ENV} />);
    await openRolePicker(user);
    expect(screen.queryByRole("option", { name })).not.toBeInTheDocument();
    local.unmount();

    render(<Login env={LIVE_DEMO_ENV} />);
    await openRolePicker(user);
    expect(screen.getByRole("option", { name })).toBeInTheDocument();
  });

  it("routes the Agency Two persona through Supabase even when the bypass is on", async () => {
    expect(LIVE_ONLY_PERSONA).toBeDefined();
    vi.mocked(signInWithPassword).mockResolvedValue("agency-two-token");
    vi.mocked(fetchCurrentUser).mockResolvedValue({
      email: LIVE_ONLY_PERSONA!.email,
      role: LIVE_ONLY_PERSONA!.role,
      agencyId: LIVE_ONLY_PERSONA!.agencyId,
    });
    const user = userEvent.setup();
    render(<Login env={FULL_DEMO_ENV} />);

    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(LIVE_ONLY_PERSONA!.name) }));
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    await waitFor(() =>
      expect(getSession()).toMatchObject({
        email: LIVE_ONLY_PERSONA!.email,
        accessToken: "agency-two-token",
        agencyId: LIVE_ONLY_PERSONA!.agencyId,
      }),
    );
    // The verified /me agency (index 1) is persisted; the bypass was NOT used for this persona.
    expect(signInWithPassword).toHaveBeenCalledWith(
      LIVE_ONLY_PERSONA!.email,
      LIVE_ONLY_PERSONA!.demoPassword,
    );
    expect(getSession()?.agencyId).toBe(DEMO_AGENCIES[1].id);
  });

  it("keeps the dev bypass on Agency One and persists its agency", async () => {
    const user = userEvent.setup();
    render(<Login env={FULL_DEMO_ENV} />);
    await openRolePicker(user);
    // Match by email — two analyst personas share the "Fraud Analyst" name under FULL_DEMO_ENV.
    await user.click(screen.getByRole("option", { name: new RegExp(DEMO_ROLES[0].email) }));
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    // A tokenless bypass session: no Supabase call, demo role set, Agency One persisted.
    expect(signInWithPassword).not.toHaveBeenCalled();
    expect(getSession()).toMatchObject({
      email: DEMO_ROLES[0].email,
      demoRole: DEMO_ROLES[0].role,
      agencyId: DEMO_AGENCIES[0].id,
    });
  });

  it("closes the picker on Escape", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} />);
    await openRolePicker(user);
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("toggles password visibility with the Show/Hide control", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} />);
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");

    await user.click(screen.getByRole("button", { name: "Show" }));
    expect(password).toHaveAttribute("type", "text");

    await user.click(screen.getByRole("button", { name: "Hide" }));
    expect(password).toHaveAttribute("type", "password");
  });

  it("keeps the submit button disabled until both fields are filled", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} />);
    const submit = screen.getByRole("button", { name: /Sign in/ });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText("Work email"), "analyst@demo-agency.test");
    await user.type(screen.getByLabelText("Password"), "demo-access-2026");
    expect(submit).toBeEnabled();
  });

  it("does not start a session when the form is submitted with empty fields", () => {
    render(<Login env={LOCAL_DEMO_ENV} />);
    fireEvent.submit(screen.getByRole("form", { name: "Sign in" }));
    expect(getSession()).toBeNull();
  });

  it("surfaces a notice from the Forgot? control without starting a session", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} />);
    await user.click(screen.getByRole("button", { name: "Forgot?" }));
    expect(getSession()).toBeNull();
  });

  it("starts a session on submit", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} />);
    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(DEMO_ROLES[0].name) }));
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    expect(getSession()).toMatchObject({ email: DEMO_ROLES[0].email, role: DEMO_ROLES[0].role });
  });

  it("signs in with Supabase and stores the server-returned role and token", async () => {
    vi.mocked(signInWithPassword).mockResolvedValue("access-token");
    vi.mocked(fetchCurrentUser).mockResolvedValue({
      email: "reviewer@example.test",
      role: "reviewer",
      agencyId: "agency-1",
    });
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} />);

    await user.type(screen.getByLabelText("Work email"), "reviewer@example.test");
    await user.type(screen.getByLabelText("Password"), "correct-password");
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    await waitFor(() =>
      expect(getSession()).toMatchObject({
        email: "reviewer@example.test",
        role: "reviewer",
        accessToken: "access-token",
        agencyId: "agency-1",
      }),
    );
    expect(signInWithPassword).toHaveBeenCalledWith("reviewer@example.test", "correct-password");
    expect(fetchCurrentUser).toHaveBeenCalledWith("access-token");
  });

  it("shows the demo picker only behind an explicit Vite-dev demo gate", () => {
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
        DEV: true,
        VITE_AUTH_DEV_BYPASS: "false",
        VITE_DEMO_AUTH_ENABLED: "true",
      }),
    ).toBe(true);
    expect(isDemoBypassEnabled({ DEV: true, VITE_AUTH_DEV_BYPASS: "false" })).toBe(false);
    expect(isLiveDemoAuthEnabled({ DEV: true, VITE_DEMO_AUTH_ENABLED: "true" })).toBe(true);
    expect(isLiveDemoAuthEnabled({ DEV: false, VITE_DEMO_AUTH_ENABLED: "true" })).toBe(false);
    expect(isLiveDemoAuthEnabled({ DEV: true, VITE_DEMO_AUTH_ENABLED: "false" })).toBe(false);
  });

  it("hides demo identities when live demo auth is not explicitly enabled", () => {
    render(<Login env={HIDDEN_DEMO_ENV} />);
    expect(screen.queryByText("Demo · sign in as")).not.toBeInTheDocument();
  });

  it("uses real Supabase auth for a live demo persona", async () => {
    vi.mocked(signInWithPassword).mockResolvedValue("demo-access-token");
    vi.mocked(fetchCurrentUser).mockResolvedValue({
      email: DEMO_ROLES[1].email,
      role: DEMO_ROLES[1].role,
      agencyId: "agency-1",
    });
    const user = userEvent.setup();
    render(<Login env={LIVE_DEMO_ENV} />);

    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(DEMO_ROLES[1].name) }));
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    await waitFor(() =>
      expect(getSession()).toMatchObject({
        email: DEMO_ROLES[1].email,
        role: DEMO_ROLES[1].role,
        accessToken: "demo-access-token",
      }),
    );
    expect(signInWithPassword).toHaveBeenCalledWith(
      DEMO_ROLES[1].email,
      DEMO_ROLES[1].demoPassword,
    );
    expect(fetchCurrentUser).toHaveBeenCalledWith("demo-access-token");
  });

  it("persists the session to localStorage only when 'keep me signed in' is checked", async () => {
    const user = userEvent.setup();
    render(<Login env={LOCAL_DEMO_ENV} />);
    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(DEMO_ROLES[0].name) }));
    await user.click(screen.getByLabelText("Keep me signed in on this device"));
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    expect(window.localStorage.getItem("fraudlens.session")).not.toBeNull();
    expect(window.sessionStorage.getItem("fraudlens.session")).toBeNull();
  });

  it("renders an animated brand motif (drawing lines, pulse-nodes, panning grid, sweep)", () => {
    const { container } = render(<Login env={LOCAL_DEMO_ENV} />);
    expect(container.querySelectorAll(".motion-safe\\:animate-draw").length).toBe(2);
    expect(container.querySelectorAll(".motion-safe\\:animate-node-pulse").length).toBe(5);
    expect(container.querySelectorAll(".motion-safe\\:animate-grid-pan").length).toBe(2);
    expect(container.querySelector(".motion-safe\\:animate-sheen")).toBeInTheDocument();
  });
});
