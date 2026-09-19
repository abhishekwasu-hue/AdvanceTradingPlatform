import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import { ASSET_CLASS_LABELS, type AssetClass, type ContractSpec } from "../types";

export default function InstrumentsPage() {
  const [instruments, setInstruments] = useState<ContractSpec[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .listInstruments()
      .then(setInstruments)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const byAssetClass = instruments.reduce<Record<string, ContractSpec[]>>((acc, spec) => {
    (acc[spec.asset_class] ??= []).push(spec);
    return acc;
  }, {});

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Instruments</h1>
        <p className="text-sm text-muted">
          MCX commodity and crypto contract specs the platform knows about, beyond plain NSE/BSE
          equity & index options (which already size correctly off your Risk Management lot
          size). These are static reference contract specifications - standard MCX lot sizes and
          common crypto tick sizes - not a live price feed. Trading a symbol below runs its
          position sizing off its own lot size (whole lots for commodities, fractional units for
          crypto) instead of your account's default lot size.
        </p>
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}

      {loading ? (
        <div className="text-sm text-muted">Loading…</div>
      ) : (
        (["COMMODITY", "CRYPTO"] as AssetClass[]).map((assetClass) => (
          <Card key={assetClass} title={`${ASSET_CLASS_LABELS[assetClass]} (${(byAssetClass[assetClass] ?? []).length})`}>
            {(byAssetClass[assetClass] ?? []).length === 0 ? (
              <div className="text-sm text-muted py-2">None registered.</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted uppercase tracking-wide">
                    <th className="pb-2 pr-4">Symbol</th>
                    <th className="pb-2 pr-4">Exchange</th>
                    <th className="pb-2 pr-4">Description</th>
                    <th className="pb-2 pr-4">Lot size</th>
                    <th className="pb-2 pr-4">Tick size</th>
                    <th className="pb-2">Fractional</th>
                  </tr>
                </thead>
                <tbody>
                  {(byAssetClass[assetClass] ?? []).map((spec) => (
                    <tr key={spec.symbol} className="border-t border-border">
                      <td className="py-2 pr-4 font-medium text-slate-200">{spec.symbol}</td>
                      <td className="py-2 pr-4 text-muted">{spec.exchange}</td>
                      <td className="py-2 pr-4 text-muted">{spec.description}</td>
                      <td className="py-2 pr-4">{spec.lot_size}</td>
                      <td className="py-2 pr-4">{spec.tick_size}</td>
                      <td className="py-2">{spec.fractional ? "Yes" : "No"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        ))
      )}
    </div>
  );
}
