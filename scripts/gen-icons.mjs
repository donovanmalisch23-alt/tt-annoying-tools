/**
 * Generates the PNG app icons with no dependencies: a tiny PNG encoder on top
 * of node:zlib, plus the same artwork as public/icons/icon.svg drawn with
 * analytic coverage so the edges are anti-aliased.
 *
 *   bun scripts/gen-icons.mjs      (or: npm run icons)
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(HERE, "..", "public", "icons");

// ---------- PNG encoding ---------- //

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buffer) {
  let c = 0xffffffff;
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const typeBuffer = Buffer.from(type, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])), 0);
  return Buffer.concat([length, typeBuffer, data, crc]);
}

function encodePng(width, height, rgba) {
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y += 1) {
    raw[y * (stride + 1)] = 0; // filter: none
    rgba.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 6; // RGBA
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ---------- drawing ---------- //

const clamp01 = (value) => (value < 0 ? 0 : value > 1 ? 1 : value);
const mix = (a, b, t) => a + (b - a) * t;

/** Signed distance to a rounded rectangle centred on (cx, cy). */
function roundedBoxDistance(x, y, cx, cy, halfWidth, halfHeight, radius) {
  const dx = Math.abs(x - cx) - (halfWidth - radius);
  const dy = Math.abs(y - cy) - (halfHeight - radius);
  const outside = Math.hypot(Math.max(dx, 0), Math.max(dy, 0));
  return outside + Math.min(Math.max(dx, dy), 0) - radius;
}

/** Distance to a circle, negative inside. */
const circleDistance = (x, y, cx, cy, radius) => Math.hypot(x - cx, y - cy) - radius;

const hex = (value) => [
  parseInt(value.slice(0, 2), 16),
  parseInt(value.slice(2, 4), 16),
  parseInt(value.slice(4, 6), 16),
];

const BG_TOP = hex("16263d");
const BG_BOTTOM = hex("070d16");
const GLOW = hex("2fd4c0");
const BAR_TOP = hex("4ff0da");
const BAR_BOTTOM = hex("1a9c96");
const DOT = hex("f4b740");
const BASELINE = hex("2fd4c0");

const BAR_HEIGHTS = [0.3, 0.4, 0.52, 0.66, 0.46, 0.36, 0.28];

function sample(x, y, size, maskable) {
  const content = maskable ? 0.66 : 0.86;
  const box = size * content;
  const cx = size / 2;
  const cy = size / 2;

  // Background: full bleed when maskable, rounded panel otherwise.
  let baseAlpha = 1;
  if (!maskable) {
    const distance = roundedBoxDistance(x, y, cx, cy, size / 2, size / 2, size * 0.22);
    baseAlpha = clamp01(0.5 - distance);
    if (baseAlpha <= 0) return [0, 0, 0, 0];
  }

  const t = clamp01(y / size);
  let r = mix(BG_TOP[0], BG_BOTTOM[0], Math.pow(t, 0.9));
  let g = mix(BG_TOP[1], BG_BOTTOM[1], Math.pow(t, 0.9));
  let b = mix(BG_TOP[2], BG_BOTTOM[2], Math.pow(t, 0.9));

  const glowDistance = Math.hypot(x - cx, y - (cy + box * 0.42));
  const glow = Math.pow(Math.max(0, 1 - glowDistance / (box * 0.78)), 2) * 0.32;
  r = mix(r, GLOW[0], glow);
  g = mix(g, GLOW[1], glow);
  b = mix(b, GLOW[2], glow);

  const barWidth = box * 0.075;
  const gap = box * 0.035;
  const count = BAR_HEIGHTS.length;
  const totalWidth = count * barWidth + (count - 1) * gap;
  const startX = cx - totalWidth / 2;
  const bottom = cy + box * 0.32;

  for (let index = 0; index < count; index += 1) {
    const height = BAR_HEIGHTS[index] * box;
    const barCx = startX + index * (barWidth + gap) + barWidth / 2;
    const barCy = bottom - height / 2;
    const distance = roundedBoxDistance(
      x,
      y,
      barCx,
      barCy,
      barWidth / 2,
      height / 2,
      barWidth * 0.45,
    );
    const coverage = clamp01(0.5 - distance);
    if (coverage <= 0) continue;
    const barT = clamp01((y - (bottom - height)) / Math.max(1, height));
    const br = mix(BAR_TOP[0], BAR_BOTTOM[0], barT);
    const bg = mix(BAR_TOP[1], BAR_BOTTOM[1], barT);
    const bb = mix(BAR_TOP[2], BAR_BOTTOM[2], barT);
    r = mix(r, br, coverage);
    g = mix(g, bg, coverage);
    b = mix(b, bb, coverage);
  }

  // Baseline strip under the bars.
  const stripDistance = roundedBoxDistance(
    x,
    y,
    cx,
    bottom + box * 0.11,
    box * 0.42,
    box * 0.014,
    box * 0.014,
  );
  const stripCoverage = clamp01(0.5 - stripDistance) * 0.5;
  if (stripCoverage > 0) {
    r = mix(r, BASELINE[0], stripCoverage);
    g = mix(g, BASELINE[1], stripCoverage);
    b = mix(b, BASELINE[2], stripCoverage);
  }

  // Status dot, upper right.
  const dotDistance = circleDistance(x, y, cx + box * 0.3, cy - box * 0.3, box * 0.075);
  const dotCoverage = clamp01(0.5 - dotDistance);
  if (dotCoverage > 0) {
    r = mix(r, DOT[0], dotCoverage);
    g = mix(g, DOT[1], dotCoverage);
    b = mix(b, DOT[2], dotCoverage);
  }

  return [r, g, b, baseAlpha * 255];
}

function render(size, maskable) {
  const pixels = Buffer.alloc(size * size * 4);
  const samples = [0.25, 0.75];
  const perSample = 1 / (samples.length * samples.length);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      let r = 0;
      let g = 0;
      let b = 0;
      let a = 0;
      for (const sy of samples) {
        for (const sx of samples) {
          const [sr, sg, sb, sa] = sample(x + sx, y + sy, size, maskable);
          r += sr * sa * perSample;
          g += sg * sa * perSample;
          b += sb * sa * perSample;
          a += sa * perSample;
        }
      }
      const offset = (y * size + x) * 4;
      // r/g/b are premultiplied sums; dividing by the alpha sum gives the
      // coverage-weighted colour.
      pixels[offset] = a > 0 ? Math.round(clamp01(r / a / 255) * 255) : 0;
      pixels[offset + 1] = a > 0 ? Math.round(clamp01(g / a / 255) * 255) : 0;
      pixels[offset + 2] = a > 0 ? Math.round(clamp01(b / a / 255) * 255) : 0;
      pixels[offset + 3] = Math.round(a);
    }
  }
  return encodePng(size, size, pixels);
}

mkdirSync(OUT_DIR, { recursive: true });

const targets = [
  ["icon-192.png", 192, false],
  ["icon-512.png", 512, false],
  ["icon-maskable-512.png", 512, true],
];

for (const [name, size, maskable] of targets) {
  const png = render(size, maskable);
  writeFileSync(resolve(OUT_DIR, name), png);
  console.log(`wrote public/icons/${name} (${size}×${size}, ${png.length} bytes)`);
}
