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

import { Login, isDemoPickerEnabled } from "./Login";
import { fetchCurrentUser } from "../lib/api";
import { DEMO_ROLES, getSession, signOut } from "../lib/session";
import { signInWithPassword } from "../lib/supabase";

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
    render(<Login />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Sign in to your account" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Work email")).toHaveValue("");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("auto-fills the email and password when a demo role is chosen", async () => {
    const user = userEvent.setup();
    render(<Login />);
    const role = DEMO_ROLES[1];

    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(role.name) }));

    expect(screen.getByLabelText("Work email")).toHaveValue(role.email);
    expect(screen.getByLabelText("Password")).toHaveValue(role.demoPassword);
  });

  it("lists every demo role once the picker is open, then closes on select", async () => {
    const user = userEvent.setup();
    render(<Login />);
    await openRolePicker(user);
    for (const role of DEMO_ROLES) {
      expect(screen.getByRole("option", { name: new RegExp(role.name) })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("option", { name: new RegExp(DEMO_ROLES[0].name) }));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("closes the picker on Escape", async () => {
    const user = userEvent.setup();
    render(<Login />);
    await openRolePicker(user);
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("toggles password visibility with the Show/Hide control", async () => {
    const user = userEvent.setup();
    render(<Login />);
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");

    await user.click(screen.getByRole("button", { name: "Show" }));
    expect(password).toHaveAttribute("type", "text");

    await user.click(screen.getByRole("button", { name: "Hide" }));
    expect(password).toHaveAttribute("type", "password");
  });

  it("keeps the submit button disabled until both fields are filled", async () => {
    const user = userEvent.setup();
    render(<Login />);
    const submit = screen.getByRole("button", { name: /Sign in/ });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText("Work email"), "analyst@demo-agency.test");
    await user.type(screen.getByLabelText("Password"), "demo-access-2026");
    expect(submit).toBeEnabled();
  });

  it("does not start a session when the form is submitted with empty fields", () => {
    render(<Login />);
    fireEvent.submit(screen.getByRole("form", { name: "Sign in" }));
    expect(getSession()).toBeNull();
  });

  it("surfaces a notice from the Forgot? control without starting a session", async () => {
    const user = userEvent.setup();
    render(<Login />);
    await user.click(screen.getByRole("button", { name: "Forgot?" }));
    expect(getSession()).toBeNull();
  });

  it("starts a session on submit", async () => {
    const user = userEvent.setup();
    render(<Login />);
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
    render(<Login />);

    await user.type(screen.getByLabelText("Work email"), "reviewer@example.test");
    await user.type(screen.getByLabelText("Password"), "correct-password");
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    await waitFor(() =>
      expect(getSession()).toMatchObject({
        email: "reviewer@example.test",
        role: "reviewer",
        accessToken: "access-token",
      }),
    );
    expect(signInWithPassword).toHaveBeenCalledWith("reviewer@example.test", "correct-password");
    expect(fetchCurrentUser).toHaveBeenCalledWith("access-token");
  });

  it("hides the demo picker outside Vite dev mode", () => {
    expect(isDemoPickerEnabled({ DEV: false })).toBe(false);
    expect(isDemoPickerEnabled({ DEV: true })).toBe(true);
  });

  it("persists the session to localStorage only when 'keep me signed in' is checked", async () => {
    const user = userEvent.setup();
    render(<Login />);
    await openRolePicker(user);
    await user.click(screen.getByRole("option", { name: new RegExp(DEMO_ROLES[0].name) }));
    await user.click(screen.getByLabelText("Keep me signed in on this device"));
    await user.click(screen.getByRole("button", { name: /Sign in/ }));

    expect(window.localStorage.getItem("fraudlens.session")).not.toBeNull();
    expect(window.sessionStorage.getItem("fraudlens.session")).toBeNull();
  });

  it("renders an animated brand motif (drawing lines, pulse-nodes, panning grid, sweep)", () => {
    const { container } = render(<Login />);
    expect(container.querySelectorAll(".motion-safe\\:animate-draw").length).toBe(2);
    expect(container.querySelectorAll(".motion-safe\\:animate-node-pulse").length).toBe(5);
    expect(container.querySelectorAll(".motion-safe\\:animate-grid-pan").length).toBe(2);
    expect(container.querySelector(".motion-safe\\:animate-sheen")).toBeInTheDocument();
  });
});
