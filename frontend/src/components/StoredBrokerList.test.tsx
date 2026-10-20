import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { StoredBrokerInfo } from "../types";
import StoredBrokerList from "./StoredBrokerList";

type ButtonProps = { onClick: () => void; children?: ReactNode };

/** The <button> elements of a rendered tree, in order, with the text they show. (No DOM in these unit tests, so the
 * component's element tree is walked and a button is "pressed" by calling its onClick.) */
function buttons(node: ReactNode): { text: string; press: () => void }[] {
  const out: { text: string; press: () => void }[] = [];
  const walk = (n: ReactNode) => {
    if (Array.isArray(n)) return n.forEach(walk);
    if (!isValidElement(n)) return;
    const el = n as ReactElement<ButtonProps>;
    if (el.type === "button") out.push({ text: renderToStaticMarkup(<>{el.props.children}</>).trim(), press: el.props.onClick });
    walk(el.props.children);
  };
  walk(node);
  return out;
}

const rows: StoredBrokerInfo[] = [
  { broker_name: "zerodha", account_label: "primary", updated_at: "2026-10-20T03:00:00Z" },
  { broker_name: "zerodha", account_label: "backup", updated_at: "2026-10-20T03:05:00Z" },
  { broker_name: "upstox", updated_at: "2026-10-20T03:10:00Z" },          // stored before labels existed
];

describe("Connected brokers: each row acts on its own account", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("Authenticate on zerodha/backup sends the backup label to the API, not the primary", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const onAuthenticate = vi.fn((broker: string, label: string) => api.authenticateBroker(broker, label));
    const tree = StoredBrokerList({ stored: rows, busy: false, onAuthenticate, onDelete: vi.fn() });
    const auth = buttons(tree).filter((b) => b.text === "Authenticate");
    expect(auth).toHaveLength(3);

    auth[1].press();
    expect(onAuthenticate).toHaveBeenLastCalledWith("zerodha", "backup");
    await onAuthenticate.mock.results[0].value;
    const [url, init] = (fetchMock.mock.calls as unknown as [string, RequestInit][])[0];
    expect(String(url)).toMatch(/\/broker\/zerodha\/authenticate\?account_label=backup$/);
    expect(init.method).toBe("POST");
  });

  it("the primary row sends primary, and a row stored without a label means the primary", () => {
    const onAuthenticate = vi.fn();
    const auth = buttons(StoredBrokerList({ stored: rows, busy: false, onAuthenticate, onDelete: vi.fn() })).filter((b) => b.text === "Authenticate");
    auth[0].press();
    auth[2].press();
    expect(onAuthenticate.mock.calls).toEqual([["zerodha", "primary"], ["upstox", "primary"]]);
  });

  it("Remove acts on the row's own label too", () => {
    const onDelete = vi.fn();
    const remove = buttons(StoredBrokerList({ stored: rows, busy: false, onAuthenticate: vi.fn(), onDelete })).filter((b) => b.text === "Remove");
    remove[1].press();
    expect(onDelete).toHaveBeenCalledWith("zerodha", "backup");
  });

  it("shows each row's label and an empty state", () => {
    const html = renderToStaticMarkup(<StoredBrokerList stored={rows} busy={false} onAuthenticate={vi.fn()} onDelete={vi.fn()} />);
    expect(html).toContain("/ backup");
    expect(renderToStaticMarkup(<StoredBrokerList stored={[]} busy={false} onAuthenticate={vi.fn()} onDelete={vi.fn()} />)).toContain("None stored yet.");
  });
});
