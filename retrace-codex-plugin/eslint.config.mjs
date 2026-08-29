import eslint from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["server/dist/**", "web/dist/**", "node_modules/**"] },
  { languageOptions: { globals: { process: "readonly" } } },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
);
