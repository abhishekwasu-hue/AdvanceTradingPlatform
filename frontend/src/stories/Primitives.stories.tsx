import type { Meta, StoryObj } from "@storybook/react";
import { Plus, Search } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Dialog, EmptyState, Input, PageHeader, Select, Sheet, Signed, Skeleton, Table, Tabs } from "../components/primitives";

const meta: Meta = { title: "Design system/Primitives" };
export default meta;
type Story = StoryObj;

export const Buttons: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="primary" icon={<Plus size={14} />}>Deploy in PAPER</Button>
      <Button>Secondary</Button>
      <Button variant="ghost">Ghost</Button>
      <Button variant="danger">Stop deployment</Button>
      <Button loading>Saving</Button>
      <Button disabled>Disabled</Button>
      <Button size="sm">Small</Button>
    </div>
  ),
};

export const Fields: Story = {
  render: function Fields() {
    const [tf, setTf] = useState("5min");
    return (
      <div className="grid max-w-xl gap-4 sm:grid-cols-2">
        <Input label="Symbol" placeholder="NIFTY 50" />
        <Input label="Risk per trade (%)" type="number" defaultValue={0.5} hint="Of your capital, per trade" />
        <Input label="Capital" type="number" error="Enter a positive amount" defaultValue={-1} />
        <Select label="Timeframe" value={tf} onChange={setTf} options={[{ value: "1min", label: "1 minute" }, { value: "5min", label: "5 minutes" }, { value: "15min", label: "15 minutes" }]} />
      </div>
    );
  },
};

export const Feedback: Story = {
  render: () => (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2"><Badge>PAPER</Badge><Badge tone="up">LONG</Badge><Badge tone="down">SHORT</Badge><Badge tone="warn">STALE 29 H</Badge><Badge tone="info">Market open</Badge><Badge tone="brand">New</Badge></div>
      <div className="flex gap-4 text-lg"><Signed value={1250} /><Signed value={-830.5} /><Signed value={0} /><Signed value={null} /></div>
      <div className="max-w-sm space-y-2"><Skeleton className="h-5 w-40" /><Skeleton /><Skeleton className="h-24 w-full" /></div>
      <EmptyState icon={<Search size={20} />} title="No deployments yet" body="Choose a template in the strategy interview and deploy it in PAPER first." action={<Button variant="primary">Open the interview</Button>} />
    </div>
  ),
};

type Row = { symbol: string; qty: number; entry: number; pnl: number };
const ROWS: Row[] = [
  { symbol: "NIFTY 24500 PE", qty: 65, entry: 112.4, pnl: 1820.5 },
  { symbol: "RELIANCE", qty: 10, entry: 1448.2, pnl: -312 },
  { symbol: "BANKNIFTY FUT", qty: 30, entry: 52310, pnl: 0 },
];
export const DataTable: Story = {
  render: () => (
    <Table<Row> caption="Open positions" rows={ROWS} rowKey={(r) => r.symbol} columns={[
      { key: "s", header: "Symbol", cell: (r) => r.symbol },
      { key: "q", header: "Qty", numeric: true, cell: (r) => r.qty },
      { key: "e", header: "Entry", numeric: true, cell: (r) => r.entry.toLocaleString("en-IN", { minimumFractionDigits: 2 }) },
      { key: "p", header: "P&L", numeric: true, cell: (r) => <Signed value={r.pnl} /> },
    ]} />
  ),
};

export const TabsAndHeader: Story = {
  render: function TabsAndHeader() {
    const [tab, setTab] = useState("today");
    return (
      <div>
        <PageHeader title="AI Copilot" description="Explains rules and data. Decisions are yours." actions={<Button variant="primary">Start</Button>} meta={<Badge tone="warn">SAMPLE DATA</Badge>} />
        <Tabs value={tab} onChange={setTab} items={[
          { value: "study", label: "Market study", content: <p className="text-sm text-fg-muted">Study</p> },
          { value: "today", label: "Today's market", content: <p className="text-sm text-fg-muted">Today</p> },
          { value: "interview", label: "Strategy interview", content: <p className="text-sm text-fg-muted">Interview</p> },
        ]} />
      </div>
    );
  },
};

export const Overlays: Story = {
  render: function Overlays() {
    const [dialog, setDialog] = useState(false);
    const [sheet, setSheet] = useState(false);
    return (
      <div className="flex gap-2">
        <Button onClick={() => setDialog(true)}>Open dialog</Button>
        <Button onClick={() => setSheet(true)}>Open sheet</Button>
        <Dialog open={dialog} onOpenChange={setDialog} title="Stop this deployment?" description="Open positions are not closed - exits keep running."
          footer={<><Button onClick={() => setDialog(false)}>Cancel</Button><Button variant="danger" onClick={() => setDialog(false)}>Stop</Button></>}>
          <p>NIFTY trend pullback · PAPER</p>
        </Dialog>
        <Sheet open={sheet} onOpenChange={setSheet} title="Position details"><p>NIFTY 24500 PE · 65 qty</p></Sheet>
      </div>
    );
  },
};
