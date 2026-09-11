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
import type { OHLCVBar, SRZone } from "../types";

export interface PriceLineSpec {
  price: number;
  color: string;
  title: string;
}

export interface ChartMarker {
  timestamp: string; // must match one candle's timestamp
  direction: "LONG" | "SHORT";
  text: string;
}

const ZONE_COLOR = { SUPPORT: "#22c55e", RESISTANCE: "#ef4444" } as const;

export default function CandleChart({
  candles,
  priceLines = [],
  zones = [],
  marker,
  height = 340,
}: {
  candles: OHLCVBar[];
  priceLines?: PriceLineSpec[];
  zones?: SRZone[];
  marker?: ChartMarker;
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
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8b96a8" },
      grid: { vertLines: { color: "#1a2333" }, horzLines: { color: "#1a2333" } },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#243044" },
      rightPriceScale: { borderColor: "#243044" },
    });
    const series = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
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

    if (marker) {
      const time = Math.floor(new Date(marker.timestamp).getTime() / 1000) as UTCTimestamp;
      series.setMarkers([
        {
          time,
          position: marker.direction === "LONG" ? "belowBar" : "aboveBar",
          color: marker.direction === "LONG" ? "#22c55e" : "#ef4444",
          shape: marker.direction === "LONG" ? "arrowUp" : "arrowDown",
          text: marker.text,
        },
      ]);
    } else {
      series.setMarkers([]);
    }
  }, [candles, priceLines, zones, marker]);

  return <div ref={containerRef} className="w-full" />;
}
