import type { Meta, StoryObj } from "@storybook/react";
import { bodyOf, type ThenIntent, type ThenTemplate } from "../screener/then/model";
import { ThenChip, ThenDrawerView } from "../screener/then/ThenPanel";
import sample from "../screener/then/thenSample.json";

/** N1 X1b - a scan rule's Then: the mode chip and the drawer (mode, confirmation candles, gate overrides, the last check per
 * symbol with its reason code, the setups sent), on the server's own sample (src/screener/then/thenSample.json). */
const meta: Meta = { title: "Alerts/Scan Then" };
export default meta;
type Story = StoryObj;

const template = sample.template as unknown as ThenTemplate;
const intents = sample.intents as unknown as ThenIntent[];
const frame = (children: JSX.Element) => <div className="max-w-3xl bg-surface p-4">{children}</div>;
const noop = () => undefined;

export const Chips: Story = {
  render: () => frame(<div className="flex gap-2"><ThenChip template={null} /><ThenChip template={template} />
    <ThenChip template={{ ...template, mode: "NOTIFY" }} /><ThenChip template={{ ...template, enabled: false }} /></div>),
};

export const DrawerWithASetup: Story = {
  render: () => frame(<ThenDrawerView template={template} intents={intents} body={bodyOf(template)} onBody={noop} errors={[]} notice=""
    busy={false} onSave={noop} onRemove={noop} />),
};

export const NewTemplate: Story = {
  render: () => frame(<ThenDrawerView template={{ ...template, configured: false, status: {} }} intents={null} body={bodyOf(null)} onBody={noop}
    errors={[]} notice="" busy={false} onSave={noop} onRemove={noop} />),
};
