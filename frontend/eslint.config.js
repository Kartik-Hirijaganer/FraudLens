// Flat ESLint config: type-aware TypeScript linting for app source, React hooks,
// jsx-a11y accessibility, Tailwind class checks, and Prettier (formatting deferred
// to Prettier). no-custom-classname is disabled because the theme defines bespoke
// design-token classes (DESIGN.md); class ORDERING is still enforced.
import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import tailwindcss from "eslint-plugin-tailwindcss";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage", "node_modules"] },
  js.configs.recommended,
  jsxA11y.flatConfigs.recommended,
  ...tailwindcss.configs["flat/recommended"],
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [...tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
      globals: { ...globals.browser },
    },
    plugins: { "react-hooks": reactHooks },
    rules: { ...reactHooks.configs.recommended.rules },
  },
  {
    files: ["*.config.{ts,js}"],
    extends: [...tseslint.configs.recommended],
    languageOptions: { globals: { ...globals.node } },
  },
  {
    // The plugin's class-ORDER and contradiction checks run against an inline
    // config so it never has to load the TypeScript tailwind.config.ts (which the
    // ESLint CJS context cannot require). The full theme lives in tailwind.config.ts
    // for the build; no-custom-classname is off since the design tokens are bespoke.
    settings: { tailwindcss: { config: { content: ["./src/**/*.{ts,tsx}"] } } },
    rules: { "tailwindcss/no-custom-classname": "off" },
  },
  prettier,
);
