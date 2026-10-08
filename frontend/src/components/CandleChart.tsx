import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { THEME_EVENT, chartColors } from "../theme";
import type { OHLCVBar, SRZone } from "../types";

export interface PriceLineSpec {
  price: number;
  color: string;
  title: string;
}

export interface ChartMarker {
  timestamp: string; // must match one candle's timestamp
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  text: string;
}

/** Convenience builder for the common case: an entry marker derived from a signal's direction. */
export function directionMarker(timestamp: string, direction: "LONG" | "SHORT", text: string): ChartMarker {
  return {
    timestamp,
    position: direction === "LONG" ? "belowBar" : "aboveBar",
    color: direction === "LONG" ? "#22c55e" : "#ef4444",
    shape: direction === "LONG" ? "arrowUp" : "arrowDown",
    text,
  };
}

const ZONE_COLOR = { SUPPORT: "#22c55e", RESISTANCE: "#ef4444" } as const;

export default function CandleChart({
  candles,
  priceLines = [],
  zones = [],
  markers = [],
  height = 340,
}: {
  candles: OHLCVBar[];
  priceLines?: PriceLineSpec[];
  zones?: SRZone[];
  markers?: ChartMarker[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const activeLinesRef = useRef<IPriceLine[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: chartColors().text,
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
      },
      grid: { vertLines: { color: chartColors().grid }, horzLines: { color: chartColors().grid } },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: chartColors().border },
      rightPriceScale: { borderColor: chartColors().border },
    });
    const c = chartColors();
    const series = chart.addCandlestickSeries({
      upColor: c.up,
      downColor: c.down,
      borderVisible: false,
      wickUpColor: c.up,
      wickDownColor: c.down,
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const handleResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener("resize", handleResize);
    handleResize();

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height]);

  // P1.2: repaint on a theme / colour-blind change.
  useEffect(() => {
    const repaint = () => {
      const c = chartColors();
      chartRef.current?.applyOptions({ layout: { textColor: c.text }, grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
                                      timeScale: { borderColor: c.border }, rightPriceScale: { borderColor: c.border } });
      seriesRef.current?.applyOptions({ upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down });
    };
    window.addEventListener(THEME_EVENT, repaint);
    return () => window.removeEventListener(THEME_EVENT, repaint);
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || candles.length === 0) return;

    const data = candles.map((c) => ({
      time: Math.floor(new Date(c.timestamp).getTime() / 1000) as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    series.setData(data);
    chart.timeScale().fitContent();

    for (const line of activeLinesRef.current) series.removePriceLine(line);
    activeLinesRef.current = [];

    for (const spec of priceLines) {
      activeLinesRef.current.push(
        series.createPriceLine({
          price: spec.price,
          color: spec.color,
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: spec.title,
        }),
      );
    }

    for (const zone of zones) {
      const color = ZONE_COLOR[zone.kind];
      for (const [price, edge] of [
        [zone.upper, "upper"],
        [zone.lower, "lower"],
      ] as const) {
        activeLinesRef.current.push(
          series.createPriceLine({
            price,
            color,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: false,
            title: `${zone.kind === "SUPPORT" ? "S" : "R"} ${edge} (${zone.source})`,
          }),
        );
      }
    }

    const sortedMarkers = [...markers].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
    );
    series.setMarkers(
      sortedMarkers.map((m) => ({
        time: Math.floor(new Date(m.timestamp).getTime() / 1000) as UTCTimestamp,
        position: m.position,
        color: m.color,
        shape: m.shape,
        text: m.text,
      })),
    );
  }, [candles, priceLines, zones, markers]);

  return <div ref={containerRef} className="w-full" />;
}
