// AUD-G1.6: ESLint + typescript-eslint for src/ (flat config). A conservative set: ESLint's and typescript-eslint's
// recommended rules plus the two classic React hooks rules (the code already carries `react-hooks/exhaustive-deps`
// disable comments). The rules the code was not clean on when this was added are warnings, held by a per-rule ratchet
// (scripts/check-eslint-ratchet.mjs, `npm run lint`): their counts may only fall. Every other rule is an error.
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/**", "storybook-static/**", "node_modules/**"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2020, globals: globals.browser },
    linterOptions: { reportUnusedDisableDirectives: "warn" },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      // Ratcheted (warn + count check), see scripts/check-eslint-ratchet.mjs.
      "react-hooks/exhaustive-deps": "warn",
      "@typescript-eslint/no-unused-vars": "warn",
      "no-useless-assignment": "warn",
      "no-control-regex": "warn",
      "no-regex-spaces": "warn",
    },
  },
);
