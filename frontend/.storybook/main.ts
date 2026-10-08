import type { StorybookConfig } from "@storybook/react-vite";

// P1.2: the design-system catalogue. `npm run storybook` (dev) / `npm run build-storybook` (static, CI).
const config: StorybookConfig = {
  stories: ["../src/**/*.stories.@(ts|tsx)"],
  addons: ["@storybook/addon-essentials", "@storybook/addon-a11y"],
  framework: { name: "@storybook/react-vite", options: {} },
  core: { disableTelemetry: true },
};
export default config;
