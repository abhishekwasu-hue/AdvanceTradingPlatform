import { HALO, HALO_ALPHA, labelSize, layoutLabels, type Shape, type Token } from "./shapes";

/**
 * Analyze a chart: "Download / share the result image" - the user's own (stored, cleaned) image with the same overlay
 * the page shows, the image-only label and the disclaimer, rendered here in the browser to a PNG. Nothing is uploaded.
 * Share uses the Web Share API with the file where the browser can share files, else the PNG downloads.
 * Colours come from the theme's tokens at the moment of export (no literal colours in the code).
 */

export function tokenColour(token: Token, alpha = 1, root: Element = document.documentElement): string {
  const raw = getComputedStyle(root).getPropertyValue(`--${token}`).trim();
  const parts = raw.split(/[\s,]+/).filter(Boolean);
  return parts.length >= 3 ? `rgb(${parts.slice(0, 3).join(" ")} / ${alpha})` : `rgb(var(--${token}) / ${alpha})`;
}

function wrap(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > maxWidth && line) { lines.push(line); line = word; } else line = next;
  }
  if (line) lines.push(line);
  return lines;
}

/** The overlay on the canvas: the shapes, then every label at the position layoutLabels gives - the very positions the
 * on-screen SVG draws (owner CA-Q3: no two labels cover each other). `height` is the image's height (not the canvas's,
 * which also holds the caption band). */
export function drawShapes(ctx: CanvasRenderingContext2D, shapes: Shape[], colour: (t: Token, a?: number) => string, fs = 12,
  height: number = ctx.canvas.height): void {
  ctx.save();
  const k = fs / 12;
  const halo = colour(HALO, HALO_ALPHA);
  for (const s of shapes) {
    ctx.setLineDash("dashed" in s && s.dashed ? [6, 4] : []);
    if (s.kind === "rect") {
      ctx.fillStyle = colour(s.token, s.fillAlpha);
      ctx.fillRect(s.x, s.y, s.w, s.h);
      ctx.strokeStyle = colour(s.token, 0.9);
      ctx.lineWidth = 1;
      ctx.strokeRect(s.x, s.y, s.w, s.h);
    } else if (s.kind === "line") {
      const dash = s.dashed ? [6, 4] : [];
      ctx.setLineDash([]);
      ctx.strokeStyle = halo;
      ctx.lineWidth = (s.width ?? 1.5) + 3;
      ctx.beginPath(); ctx.moveTo(s.x0, s.y0); ctx.lineTo(s.x1, s.y1); ctx.stroke();
      ctx.setLineDash(dash);
      ctx.strokeStyle = colour(s.token);
      ctx.lineWidth = s.width ?? 1.5;
      ctx.beginPath(); ctx.moveTo(s.x0, s.y0); ctx.lineTo(s.x1, s.y1); ctx.stroke();
    } else if (s.kind === "dot") {
      for (const [stroke, width] of [[halo, 5], [colour(s.token), 2]] as const) {
        ctx.strokeStyle = stroke;
        ctx.lineWidth = width;
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); ctx.stroke();
      }
    }
  }
  ctx.setLineDash([]);
  for (const l of layoutLabels(shapes, height, fs)) {
    ctx.font = `${l.bold ? "bold " : ""}${fs}px sans-serif`;
    ctx.textAlign = l.anchor === "end" ? "right" : "left";
    ctx.lineWidth = 3 * k; ctx.strokeStyle = halo; ctx.strokeText(l.text, l.x, l.y);
    ctx.fillStyle = colour(l.token); ctx.fillText(l.text, l.x, l.y);
  }
  ctx.restore();
}

export interface ExportInput { image: CanvasImageSource; width: number; height: number; shapes: Shape[]; label: string | null; disclaimer: string }

/** The annotated result as a PNG Blob: the image with its overlay, then a caption band (label + disclaimer). */
export async function renderResultPng(input: ExportInput, colour: (t: Token, a?: number) => string = tokenColour): Promise<Blob> {
  const canvas = document.createElement("canvas");
  const probe = canvas.getContext("2d");
  if (!probe) throw new Error("This browser cannot draw the image.");
  const fs = labelSize(input.width);
  const cap = Math.max(13, Math.round(fs * 0.9));          // owner review (3 Nov): the caption grows with the image too
  probe.font = `${cap}px sans-serif`;
  const captions = [...(input.label ? wrap(probe, input.label, input.width - 24) : []), ...wrap(probe, input.disclaimer, input.width - 24)];
  const band = Math.round(cap * 1.3) + captions.length * Math.round(cap * 1.4);
  canvas.width = input.width;
  canvas.height = input.height + band;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser cannot draw the image.");
  ctx.drawImage(input.image, 0, 0, input.width, input.height);
  drawShapes(ctx, input.shapes, colour, fs, input.height);
  ctx.fillStyle = "rgb(255 255 255)";   // the caption band is always light, so the export reads the same in any theme
  ctx.fillRect(0, input.height, input.width, band);
  ctx.fillStyle = "rgb(15 23 42)";
  ctx.font = `${cap}px sans-serif`;
  captions.forEach((line, i) => ctx.fillText(line, 12, input.height + Math.round(cap * 1.7) + i * Math.round(cap * 1.4)));
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("The image could not be made."))), "image/png"));
}

/** Shares the PNG where the browser can share files (phones), else downloads it. Returns what happened. */
export async function shareOrDownload(blob: Blob, filename: string): Promise<"shared" | "downloaded"> {
  const file = typeof File === "function" ? new File([blob], filename, { type: "image/png" }) : null;
  const nav = navigator as Navigator & { canShare?: (d: ShareData) => boolean };
  if (file && typeof nav.share === "function" && nav.canShare?.({ files: [file] })) {
    try {
      await nav.share({ files: [file], title: "Chart analysis" });
      return "shared";
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return "shared";
    }
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return "downloaded";
}
