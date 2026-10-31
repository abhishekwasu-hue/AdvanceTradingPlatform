import { deflateSync } from "node:zlib";

/** A drawn placeholder chart PNG (no real chart, no real prices) for the chart-analyze e2e tests. */
const CRC = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = (buf: Buffer) => { let c = 0xffffffff; for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
function chunk(type: string, data: Buffer): Buffer {
  const head = Buffer.alloc(8); head.writeUInt32BE(data.length, 0); head.write(type, 4, "latin1");
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
}
export function drawnChartPng(width = 640, height = 360): Buffer {
  const row = width * 3 + 1;
  const px = Buffer.alloc(row * height, 0);
  const set = (x: number, y: number, rgb: [number, number, number]) => { if (x >= 0 && y >= 0 && x < width && y < height) px.set(rgb, y * row + 1 + x * 3); };
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) set(x, y, x % 64 === 0 || y % 60 === 0 ? [228, 231, 236] : [250, 250, 250]);
  let level = height / 2;
  for (let i = 0; i < 20; i++) {
    const x0 = 20 + i * 30, top = Math.round(level - 25 + ((i * 37) % 11)), bottom = Math.round(level + 12);
    const colour: [number, number, number] = i % 3 ? [22, 150, 92] : [214, 56, 56];
    for (let y = top - 8; y < bottom + 8; y++) set(x0 + 6, y, [60, 60, 60]);
    for (let y = top; y < bottom; y++) for (let x = x0; x < x0 + 12; x++) set(x, y, colour);
    level += i < 12 ? -6 : 7;
  }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", deflateSync(px)), chunk("IEND", Buffer.alloc(0))]);
}
