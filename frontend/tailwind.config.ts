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
    },
  },
  plugins: [],
};

export default config;
