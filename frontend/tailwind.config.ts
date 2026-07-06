/**
 * Summary: Tailwind theme translating the `wise` design system (DESIGN.md) into
 * Tailwind tokens — the SOLE source of styling values. Colors, radii, spacing, and
 * the display/body type scale all map 1:1 to DESIGN.md so components never use
 * ad-hoc hex, px, or off-scale values. Wise green (`primary`, #9fe870) is the only
 * brand accent and is reserved for the primary CTA (never as a success color —
 * `positive` covers that). `xl` (24px) is the canonical card/button radius.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - default: the Tailwind Config object.
 *
 * Notes:
 * - Re-theme ONLY by re-running `npx getdesign@latest add wise` and re-deriving here.
 */
import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: "#9fe870",
          active: "#cdffad",
          neutral: "#c5edab",
          pale: "#e2f6d5",
        },
        "on-primary": "#0e0f0c",
        ink: { DEFAULT: "#0e0f0c", deep: "#163300" },
        body: "#454745",
        mute: "#868685",
        canvas: { DEFAULT: "#ffffff", soft: "#e8ebe6" },
        positive: { DEFAULT: "#2ead4b", deep: "#054d28" },
        warning: { DEFAULT: "#ffd11a", deep: "#b86700", content: "#4a3b1c" },
        negative: { DEFAULT: "#d03238", deep: "#a72027", darkest: "#a7000d", bg: "#320707" },
        accent: { orange: "#ffc091", cyan: "#38c8ff" },
        // Auth-screen palette — a self-contained slate/sky theme for the pre-auth login
        // matching the approved design brief (deliberately distinct from the wise tokens).
        auth: {
          panel: "#111827",
          surface: "#fafafa",
          border: "#e5e7eb",
          "border-strong": "#d1d5db",
          divider: "#f3f4f6",
          strong: "#374151",
          muted: "#6b7280",
          faint: "#9ca3af",
          cyan: "#38bdf8",
          amber: "#fbbf24",
          green: "#34d399",
        },
      },
      backgroundImage: {
        "auth-glow":
          "radial-gradient(circle at 72% 42%, rgba(56,189,248,0.16), transparent 46%), radial-gradient(circle at 20% 78%, rgba(251,191,36,0.10), transparent 42%)",
        "auth-grid-coarse":
          "linear-gradient(rgba(255,255,255,0.09) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.09) 1px, transparent 1px)",
        "auth-grid-fine":
          "linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px)",
        "auth-sheen": "linear-gradient(90deg, transparent, rgba(255,255,255,0.07), transparent)",
        "auth-sheen-light": "linear-gradient(90deg, transparent, rgba(17,24,39,0.03), transparent)",
      },
      boxShadow: {
        "auth-focus": "0 0 0 3px rgba(17,24,39,0.08)",
        "auth-menu": "0 12px 32px rgba(17,24,39,0.14)",
        "node-cyan": "0 0 12px rgba(56,189,248,0.7)",
        "node-amber": "0 0 10px rgba(251,191,36,0.6)",
        "node-green": "0 0 10px rgba(52,211,153,0.6)",
      },
      borderRadius: {
        none: "0px",
        sm: "8px",
        md: "12px",
        lg: "16px",
        xl: "24px",
        pill: "9999px",
        full: "9999px",
      },
      spacing: {
        xxs: "2px",
        xs: "4px",
        sm: "8px",
        md: "12px",
        lg: "16px",
        xl: "24px",
        "2xl": "32px",
        "3xl": "48px",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        display: ["Manrope", "Inter", "system-ui", "sans-serif"],
      },
      maxWidth: {
        // DESIGN.md: the marketing container centres at ~1200px.
        container: "1200px",
        // The analyst app shell uses a wider working area than the marketing
        // container so dense triage screens fill large displays.
        shell: "1600px",
      },
      fontSize: {
        "display-mega": ["126px", { lineHeight: "107.1px", fontWeight: "900" }],
        "display-xxl": ["96px", { lineHeight: "81.6px", fontWeight: "900" }],
        "display-xl": ["64px", { lineHeight: "54.4px", fontWeight: "900" }],
        "display-lg": ["47px", { lineHeight: "70.5px", fontWeight: "400" }],
        "display-md": ["40px", { lineHeight: "34px", fontWeight: "900" }],
        "display-sm": ["32px", { lineHeight: "38.4px", fontWeight: "600" }],
        "display-xs": ["24px", { lineHeight: "31.2px", fontWeight: "600" }],
        "body-lg": ["20px", { lineHeight: "30px" }],
        "body-md": ["16px", { lineHeight: "24px" }],
        "body-sm": ["14px", { lineHeight: "20px" }],
        caption: ["12px", { lineHeight: "16px" }],
        "button-md": ["16px", { lineHeight: "24px", fontWeight: "600" }],
      },
      // Motion for the login screen (decorative + entrance), gated behind `motion-safe:`
      // so reduced-motion users get the static final frame. Mirrors the design brief.
      keyframes: {
        "grid-pan": {
          from: { backgroundPosition: "0 0" },
          to: { backgroundPosition: "44px 44px" },
        },
        draw: {
          from: { strokeDashoffset: "1700" },
          to: { strokeDashoffset: "0" },
        },
        "node-pulse": {
          "0%, 100%": { opacity: "0.55", transform: "scale(1)" },
          "50%": { opacity: "1", transform: "scale(1.35)" },
        },
        sheen: {
          from: { transform: "translateX(-120%)" },
          to: { transform: "translateX(220%)" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(14px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
      },
      animation: {
        "grid-pan": "grid-pan 7s linear infinite",
        draw: "draw 3s ease-out forwards",
        "node-pulse": "node-pulse 2.6s ease-in-out infinite",
        sheen: "sheen 6s ease-in-out 1.2s infinite",
        "fade-up": "fade-up 0.6s ease-out both",
        "fade-in": "fade-in 0.8s ease-out both",
      },
    },
  },
  plugins: [],
};

export default config;
