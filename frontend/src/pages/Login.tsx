/**
 * Summary: The FraudLens sign-in screen (plan §16 Phase 11) — the pre-auth gate rendered
 * by the shell whenever there is no session. Matches the approved design brief: a navy brand
 * panel (wordmark, animated grid motif, product promise) beside a light sign-in form. The
 * "Demo · sign in as" picker lists the synthetic `DEMO_ROLES` and auto-fills the email +
 * password when a role is chosen, so the demo build can be entered without typing credentials.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Login: render the split-panel sign-in screen and start a session on submit.
 *
 * Notes:
 * - Auto-filled credentials are synthetic demo values (see session.ts) — no PHI, no real secret.
 * - The login uses its own `auth-*` slate/sky palette (tailwind.config) to match the design brief,
 *   deliberately distinct from the wise tokens used by the signed-in shell.
 * - All motion is gated behind `motion-safe:` so reduced-motion users get the static final frame.
 */
import { useEffect, useRef, useState } from "react";

import { cx } from "../lib/cx";
import { DEMO_ROLES, signIn, type DemoRole, type RoleAccent } from "../lib/session";
import { notify } from "../lib/toast";

const ACCENT_DOT: Record<RoleAccent, string> = {
  green: "bg-auth-green",
  cyan: "bg-auth-cyan",
  amber: "bg-auth-amber",
  slate: "bg-auth-faint",
};

const INPUT_CLASS =
  "border-auth-border focus:border-auth-panel focus:shadow-auth-focus w-full rounded-sm border bg-canvas px-lg py-md text-body-sm text-auth-panel outline-none transition placeholder:text-auth-faint";

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(false);
  const [roleId, setRoleId] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  const selectedRole = DEMO_ROLES.find((r) => r.id === roleId) ?? null;
  const canSubmit = email.trim().length > 0 && password.length > 0;

  useEffect(() => {
    if (!menuOpen) {
      return;
    }
    function onPointer(event: MouseEvent): void {
      if (pickerRef.current && !pickerRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  function handlePick(role: DemoRole): void {
    setRoleId(role.id);
    setEmail(role.email);
    setPassword(role.demoPassword);
    setMenuOpen(false);
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    signIn(email.trim(), remember, selectedRole?.role);
  }

  return (
    <div className="bg-auth-surface text-auth-panel flex min-h-screen flex-col font-sans lg:flex-row">
      {/* Brand panel */}
      <aside className="bg-auth-panel p-2xl relative hidden overflow-hidden text-white lg:flex lg:grow lg:basis-[46%] lg:flex-col">
        <BrandMotif />
        <a href="/" className="gap-md motion-safe:animate-fade-in relative z-10 flex items-center">
          <span className="bg-canvas text-auth-panel flex size-[40px] items-center justify-center rounded-[10px] text-[15px] font-bold">
            FL
          </span>
          <span className="text-[18px] font-bold tracking-tight">FraudLens</span>
        </a>
        <div className="gap-lg relative z-10 mt-auto flex max-w-[420px] flex-col">
          <span className="text-caption motion-safe:animate-fade-up font-semibold uppercase tracking-[0.14em] text-white/45">
            AML investigation desk
          </span>
          <h2
            className="motion-safe:animate-fade-up text-[34px] font-semibold leading-[1.18] tracking-tight"
            style={{ animationDelay: "0.2s" }}
          >
            Every transaction, scored the moment it lands.
          </h2>
          <p
            className="text-body-md motion-safe:animate-fade-up leading-relaxed text-white/60"
            style={{ animationDelay: "0.3s" }}
          >
            Sign in to triage alerts, review AI-drafted SARs, and operate the scoring model — with a
            person on every decision.
          </p>
        </div>
      </aside>

      {/* Sign-in panel */}
      <main className="p-2xl relative flex grow basis-full items-center justify-center overflow-hidden lg:basis-[54%]">
        <div
          aria-hidden="true"
          className="bg-auth-sheen-light motion-safe:animate-sheen pointer-events-none absolute inset-y-0 w-2/5 translate-x-[-120%]"
        />
        <div className="relative w-full max-w-[392px]">
          <div className="gap-sm mb-2xl motion-safe:animate-fade-up flex flex-col">
            <span className="text-caption text-auth-muted font-semibold uppercase tracking-[0.14em]">
              Welcome back
            </span>
            <h1 className="text-auth-panel text-[26px] font-semibold tracking-tight">
              Sign in to your account
            </h1>
          </div>

          <form className="gap-lg flex flex-col" onSubmit={handleSubmit} aria-label="Sign in">
            <div className="gap-sm flex flex-col">
              <label
                htmlFor="login-email"
                className="text-caption text-auth-muted font-semibold uppercase tracking-widest"
              >
                Work email
              </label>
              <input
                id="login-email"
                type="email"
                autoComplete="username"
                required
                placeholder="you@agency.gov"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={INPUT_CLASS}
              />
            </div>

            <div className="gap-sm flex flex-col">
              <div className="flex items-center justify-between">
                <label
                  htmlFor="login-password"
                  className="text-caption text-auth-muted font-semibold uppercase tracking-widest"
                >
                  Password
                </label>
                <button
                  type="button"
                  onClick={() =>
                    notify({
                      tone: "neutral",
                      title: "Password reset",
                      description: "Not available in the demo build.",
                    })
                  }
                  className="text-auth-muted hover:text-auth-panel text-[12px] font-medium transition"
                >
                  Forgot?
                </button>
              </div>
              <div className="relative">
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  placeholder="••••••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={cx(INPUT_CLASS, "pr-[64px]")}
                />
                <button
                  type="button"
                  aria-pressed={showPassword}
                  onClick={() => setShowPassword((v) => !v)}
                  className="text-auth-muted px-sm py-xs absolute right-[6px] top-1/2 -translate-y-1/2 rounded-sm font-mono text-[11px] font-semibold uppercase tracking-wide"
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
            </div>

            <label htmlFor="login-remember" className="gap-sm flex cursor-pointer items-center">
              <input
                id="login-remember"
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="accent-auth-panel size-lg cursor-pointer"
              />
              <span className="text-body-sm text-auth-strong">
                Keep me signed in on this device
              </span>
            </label>

            <button
              type="submit"
              disabled={!canSubmit}
              className="bg-auth-panel gap-sm mt-xs py-md text-body-sm group flex w-full items-center justify-center rounded-sm font-semibold text-white transition hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-50"
            >
              Sign in
              <span className="inline-block transition-transform group-hover:translate-x-1">→</span>
            </button>
          </form>

          {/* Demo role picker */}
          <div
            ref={pickerRef}
            className="border-auth-border gap-sm mt-xl pt-xl flex flex-col border-t border-dashed"
          >
            <div className="flex items-center justify-between">
              <span
                id="login-role-label"
                className="text-caption text-auth-muted font-semibold uppercase tracking-widest"
              >
                Demo · sign in as
              </span>
              <span className="text-auth-faint font-mono text-[10px]">credentials auto-filled</span>
            </div>
            <div className="relative">
              <button
                type="button"
                aria-haspopup="listbox"
                aria-expanded={menuOpen}
                aria-labelledby="login-role-label"
                onClick={() => setMenuOpen((v) => !v)}
                className={cx(
                  "border-auth-border hover:border-auth-border-strong gap-md flex w-full items-center rounded-sm border bg-canvas px-lg py-md text-left transition",
                  menuOpen && "border-auth-panel shadow-auth-focus",
                )}
              >
                <span
                  className={cx(
                    "size-[8px] shrink-0 rounded-full",
                    selectedRole ? ACCENT_DOT[selectedRole.accent] : "bg-auth-faint",
                  )}
                />
                <span
                  className={cx(
                    "text-body-sm grow font-medium",
                    selectedRole ? "text-auth-panel" : "text-auth-faint",
                  )}
                >
                  {selectedRole ? selectedRole.name : "Choose a role to auto-fill…"}
                </span>
                <span
                  className={cx(
                    "text-auth-faint text-[12px] transition-transform",
                    menuOpen && "rotate-180",
                  )}
                >
                  ⌄
                </span>
              </button>

              {menuOpen && (
                <div
                  role="listbox"
                  aria-label="Demo roles"
                  className="border-auth-border shadow-auth-menu motion-safe:animate-fade-up bg-canvas absolute inset-x-0 bottom-[calc(100%+8px)] z-20 overflow-hidden rounded-[10px] border"
                >
                  {DEMO_ROLES.map((role, i) => (
                    <button
                      key={role.id}
                      type="button"
                      role="option"
                      aria-selected={role.id === roleId}
                      onClick={() => handlePick(role)}
                      className={cx(
                        "border-auth-divider hover:bg-auth-divider gap-md flex w-full items-center px-lg py-md text-left transition",
                        i < DEMO_ROLES.length - 1 && "border-b",
                      )}
                    >
                      <span
                        className={cx("size-[8px] shrink-0 rounded-full", ACCENT_DOT[role.accent])}
                      />
                      <span className="gap-xxs flex grow flex-col">
                        <span className="text-body-sm text-auth-panel font-medium">
                          {role.name}
                        </span>
                        <span className="text-auth-faint font-mono text-[11px]">{role.email}</span>
                      </span>
                      <span className="text-auth-muted bg-auth-divider px-sm py-xxs rounded-full text-[9px] font-semibold uppercase tracking-wide">
                        {role.tag}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="mt-xl flex items-center justify-between">
            <span className="text-caption text-auth-faint gap-xs inline-flex items-center font-mono">
              <span aria-hidden="true" className="bg-auth-green size-[6px] rounded-full" />
              Encrypted · SOC 2 Type II
            </span>
            <span className="text-caption text-auth-faint">Personal demo build</span>
          </div>
        </div>
      </main>
    </div>
  );
}

// The two right-angle connector paths drawn across the grid.
const MOTIF_LINES = [
  "0,620 112,620 112,340 280,340 280,508 448,508 448,228 640,228",
  "0,760 168,760 168,600 336,600 336,676 560,676 560,452 640,452",
] as const;

// Pulse-nodes positioned over the grid; each breathes on its own duration/phase.
const MOTIF_NODES = [
  {
    pos: "right-[80px] top-1/4",
    size: "size-[10px]",
    tone: "bg-auth-cyan shadow-node-cyan",
    dur: "2.4s",
    delay: "0s",
  },
  {
    pos: "right-[180px] top-[56%]",
    size: "size-[8px]",
    tone: "bg-auth-amber shadow-node-amber",
    dur: "3s",
    delay: "0.5s",
  },
  {
    pos: "left-[140px] top-[33%]",
    size: "size-[7px]",
    tone: "bg-canvas/70",
    dur: "3.1s",
    delay: "0.9s",
  },
  {
    pos: "left-[220px] top-[70%]",
    size: "size-[8px]",
    tone: "bg-auth-green shadow-node-green",
    dur: "2.7s",
    delay: "1.3s",
  },
  {
    pos: "right-[300px] top-[80%]",
    size: "size-[6px]",
    tone: "bg-canvas/50",
    dur: "3.4s",
    delay: "0.3s",
  },
] as const;

function BrandMotif() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="bg-auth-glow absolute inset-0" />
      <div className="bg-auth-grid-coarse motion-safe:animate-grid-pan absolute inset-0 bg-[length:56px_56px] opacity-90" />
      <div className="bg-auth-grid-fine motion-safe:animate-grid-pan absolute inset-0 bg-[length:14px_14px] opacity-50" />
      <svg
        viewBox="0 0 640 900"
        preserveAspectRatio="none"
        className="absolute inset-0 size-full text-white opacity-90"
      >
        {MOTIF_LINES.map((points, i) => (
          <polyline
            key={points}
            points={points}
            fill="none"
            stroke={i === 0 ? undefined : "currentColor"}
            className={cx(i === 0 && "stroke-auth-cyan", "motion-safe:animate-draw")}
            strokeWidth="1.5"
            strokeOpacity={i === 0 ? "0.42" : "0.22"}
            strokeDasharray="1700"
            style={{
              animationDuration: i === 0 ? "3s" : "3.2s",
              animationDelay: i === 0 ? "0.4s" : "0.7s",
            }}
          />
        ))}
      </svg>
      <div className="bg-auth-sheen motion-safe:animate-sheen absolute inset-y-0 w-[180px] translate-x-[-140%]" />
      {MOTIF_NODES.map((node) => (
        <span
          key={node.pos}
          className={cx(
            "motion-safe:animate-node-pulse absolute rounded-full",
            node.pos,
            node.size,
            node.tone,
          )}
          style={{ animationDuration: node.dur, animationDelay: node.delay }}
        />
      ))}
    </div>
  );
}
