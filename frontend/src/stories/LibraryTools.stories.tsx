import type { Meta, StoryObj } from "@storybook/react";
import { LearnPanel } from "../screener/LearnPanel";
import { LibraryTools } from "../screener/LibraryTools";
import { mirrorsByScreen } from "../screener/library";
import {
  SAMPLE_CONFLUENCE, SAMPLE_DIGEST, SAMPLE_EXAMPLES, SAMPLE_LEARN, SAMPLE_LISTS, SAMPLE_MIRRORS, SAMPLE_REGIME, SAMPLE_WATCH,
} from "../screener/librarySample";
import { ScreenGallery } from "../screener/ScreenGallery";
import sample from "../screener/taxonomySample.json";
import type { Taxonomy } from "../screener/taxonomy";

/** U9 TX2 - the library tools: the confluence view, watch-then-trigger lists and the mirror line on gallery cards. */
const meta: Meta = { title: "Screener/Library tools" };
export default meta;
type Story = StoryObj;

const tax = sample as unknown as Taxonomy;
const frame = (children: JSX.Element) => <div className="max-w-5xl bg-surface p-4">{children}</div>;

export const Confluence: Story = {
  render: () => frame(<LibraryTools taxonomy={tax} mirrors={SAMPLE_MIRRORS} watchLists={SAMPLE_LISTS} symbolsText="UP, DOWN, FLAT" initialConfluence={SAMPLE_CONFLUENCE} />),
};

export const WatchThenTrigger: Story = {
  render: () => frame(<LibraryTools taxonomy={tax} mirrors={SAMPLE_MIRRORS} watchLists={SAMPLE_LISTS} symbolsText="UP, MID, DOWN" initialTab="watch" initialWatch={SAMPLE_WATCH} />),
};

export const GalleryWithMirrors: Story = {
  render: () => frame(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} mirrors={mirrorsByScreen(SAMPLE_MIRRORS)} />),
};

export const GalleryWithTodaysRegime: Story = {
  render: () => frame(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} regime={SAMPLE_REGIME} />),
};

export const DailyDigest: Story = {
  render: () => frame(<LibraryTools taxonomy={tax} mirrors={SAMPLE_MIRRORS} watchLists={SAMPLE_LISTS} symbolsText="" digest={SAMPLE_DIGEST} initialTab="digest" />),
};

export const LearnMode: Story = {
  render: () => frame(<LearnPanel learn={SAMPLE_LEARN} onBack={() => undefined} initialExamples={SAMPLE_EXAMPLES} />),
};
