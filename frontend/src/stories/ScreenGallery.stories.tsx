import type { Meta, StoryObj } from "@storybook/react";
import { ScreenGallery } from "../screener/ScreenGallery";
import sample from "../screener/taxonomySample.json";
import type { Taxonomy } from "../screener/taxonomy";

/** U9 TX1 - the screen library: method rail, setup chips, cards and the method × setup matrix, on the server's own
 * taxonomy sample (src/screener/taxonomySample.json). Every story follows the toolbar's theme and colour-blind setting. */
const meta: Meta = { title: "Screener/Gallery" };
export default meta;
type Story = StoryObj;

const tax = sample as unknown as Taxonomy;
const frame = (children: JSX.Element) => <div className="max-w-6xl bg-surface p-4">{children}</div>;

export const AllScreens: Story = {
  render: () => frame(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} />),
};

export const OneCell: Story = {
  render: () => frame(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} initial={{ method: "indicator", setup: "momentum" }} />),
};

export const AnEmptyCell: Story = {
  render: () => frame(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} initial={{ method: "price_action", setup: "breakout" }} />),
};

export const MyScreens: Story = {
  render: () => frame(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} initial={{ method: "my_screens" }} />),
};

export const CannotOpenHere: Story = {
  render: () => frame(<ScreenGallery taxonomy={tax} canOpen={() => false} onOpen={() => undefined} />),
};
