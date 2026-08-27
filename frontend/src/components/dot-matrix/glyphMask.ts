export interface DotCloud {
  width: number;
  height: number;
  step: number;
  size: number;
  count: number;
  positions: Float32Array;
  cells: Uint16Array;
  staticNoise: Float32Array;
}

function hash(x: number, y: number): number {
  const value = Math.sin(x * 127.1 + y * 311.7) * 43_758.5453;
  return value - Math.floor(value);
}

/** Rasterize daypilot once, retaining only grid cells that fall inside the glyph mask. */
export function createDotCloud(width: number, height: number): DotCloud {
  const mask = document.createElement("canvas");
  mask.width = Math.max(1, Math.round(width));
  mask.height = Math.max(1, Math.round(height));
  const context = mask.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("Canvas 2D context is unavailable");

  const wordmark = "daypilot";
  const maxWidth = width * 0.98;
  let fontSize = height * 0.85;
  const configuredFont = getComputedStyle(document.documentElement)
    .getPropertyValue("--font-wordmark")
    .trim();
  const fontFamily = configuredFont || '"Geist Mono", "SFMono-Regular", monospace';
  context.font = `640 ${fontSize}px ${fontFamily}`;
  const measured = () => context.measureText(wordmark);
  let metrics = measured();
  if (metrics.width > maxWidth) fontSize *= maxWidth / metrics.width;
  context.font = `640 ${fontSize}px ${fontFamily}`;
  metrics = measured();

  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillStyle = "#fff";
  context.fillText(wordmark, width / 2, height / 2 + fontSize * 0.02);

  const pixels = context.getImageData(0, 0, mask.width, mask.height).data;
  // A 3px cell with a 1px gap, matching the reference curtain.
  const size = 3;
  const step = 4;
  const columns = Math.floor((width + 1) / step);
  const rows = Math.floor((height + 1) / step);
  const gridWidth = columns > 0 ? columns * size + (columns - 1) : 0;
  const gridHeight = rows > 0 ? rows * size + (rows - 1) : 0;
  const offsetX = Math.round((width - gridWidth) / 2);
  const offsetY = Math.round((height - gridHeight) / 2);
  const coordinates: number[] = [];
  const cells: number[] = [];
  const staticNoise: number[] = [];
  for (let row = 0; row < rows; row += 1) {
    const y = offsetY + row * step;
    for (let column = 0; column < columns; column += 1) {
      const x = offsetX + column * step;
      const pixelX = Math.min(mask.width - 1, Math.max(0, Math.round(x + size / 2)));
      const pixelY = Math.min(mask.height - 1, Math.max(0, Math.round(y + size / 2)));
      if (pixels[(pixelY * mask.width + pixelX) * 4 + 3] < 96) continue;
      coordinates.push(x, y);
      cells.push(column, row);
      staticNoise.push(hash(column, row));
    }
  }
  const count = staticNoise.length;
  return {
    width,
    height,
    step,
    size,
    count,
    positions: Float32Array.from(coordinates),
    cells: Uint16Array.from(cells),
    staticNoise: Float32Array.from(staticNoise),
  };
}
