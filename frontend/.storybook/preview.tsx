import type { Decorator, Preview } from "@storybook/react";
import { useEffect } from "react";
import "../src/styles/index.css";

/** Every story renders in both themes and with the colour-blind option, from the toolbar. */
const withAppearance: Decorator = (Story, ctx) => {
  const theme = ctx.globals.theme as string;
  const cvd = ctx.globals.cvd as string;
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.cvd = cvd;
  }, [theme, cvd]);
  return <div className="min-h-[200px] bg-surface p-6 text-fg"><Story /></div>;
};

const preview: Preview = {
  decorators: [withAppearance],
  globalTypes: {
    theme: { description: "Theme", toolbar: { title: "Theme", icon: "mirror", items: ["dark", "light"], dynamicTitle: true } },
    cvd: { description: "Colour-blind profit/loss", toolbar: { title: "Colour-blind", icon: "eye", items: ["off", "on"], dynamicTitle: true } },
  },
  initialGlobals: { theme: "dark", cvd: "off" },
  parameters: { layout: "fullscreen", controls: { expanded: true } },
};
export default preview;
