import type { Meta, StoryObj } from "@storybook/react";
import { PortfolioRiskView } from "../portfolio/PortfolioRiskCard";
import type { PortfolioRiskView as View } from "../portfolio/model";
import sample from "../portfolio/riskSample.json";

/** N1 F3 (flag `portfolio_risk`): the Positions page's portfolio risk card. Use the toolbar for dark / light. The
 * payload is the server's own (portfolio/riskSample.json). */
const meta: Meta = { title: "Portfolio/Risk card" };
export default meta;
type Story = StoryObj;

const view = sample as unknown as View;
const frame = (child: JSX.Element) => <div className="max-w-3xl rounded-lg border border-border bg-surface-1 p-3">{child}</div>;

export const Book: Story = { render: () => frame(<PortfolioRiskView view={view} />) };
export const OneSided: Story = {
  render: () => frame(<PortfolioRiskView view={{ ...view, direction: { bullish: 4, bearish: 0, neutral: 0,
    warning: "All 4 directional positions are bullish: one large move against them hits them together (correlated risk, no diversification)." } }} />),
};
export const CapsOff: Story = { render: () => frame(<PortfolioRiskView view={{ ...view, limits: null }} />) };
export const Empty: Story = { render: () => frame(<PortfolioRiskView view={{ ...view, positions: 0, buckets: [] }} />) };
