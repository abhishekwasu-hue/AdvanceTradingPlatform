import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import contract from "../screener/builderTexts.json";
import { FunnelCanvas } from "../screener/FunnelCanvas";
import { stagesFromServer } from "../screener/importStages";
import type { Registry, Stage } from "../screener/model";

/** U2 - the formula block (flag `screener_builder_u2`): a Legends screen opened in the builder; the conjuncts no other
 * block form writes back exactly stay formula blocks. */
const meta: Meta = { title: "Screener/Formula block" };
export default meta;
type Story = StoryObj;

const reg = contract.registry as unknown as Registry;
const opened = stagesFromServer(reg, [
  { text: "close > SMA(close, 30)", wrapped: "close > SMA(close, 30)", ast: { k: "Compare", op: ">", left: { k: "Field", name: "close" }, right: { k: "Call", name: "SMA", args: [{ k: "Field", name: "close" }, { k: "Num", v: 30 }] } } },
  { text: "SMA(close, 30) >= SMA(close, 30)[1]", wrapped: "SMA(close, 30) >= SMA(close, 30)[1]", ast: { k: "Compare", op: ">=" } },
  { text: "close > Max(high, 10)[1]", wrapped: "close > Max(high, 10)[1]", ast: { k: "Compare", op: ">" } },
  { text: "volume >= 2 * SMA(volume, 4)[1]", wrapped: "volume >= 2 * SMA(volume, 4)[1]", ast: { k: "Compare", op: ">=" } },
]);

function Canvas() {
  const [stages, setStages] = useState<Stage[]>(opened);
  return (
    <div className="max-w-4xl bg-surface p-4">
      <FunnelCanvas stages={stages} registry={reg} scanTf="1w" universe={50} funnel={null} fresh={false} matched={null} problems={{}}
                    onChange={setStages} formula />
    </div>
  );
}

export const OpenedLegendsScreen: Story = { render: () => <Canvas /> };
