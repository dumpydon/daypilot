const C_X = 0.211324865405187;
const C_Y = 0.366025403784439;
const C_Z = -0.577350269189626;
const C_W = 0.024390243902439;

function fract(value: number): number {
  return value - Math.floor(value);
}

function mod289(value: number): number {
  return value - Math.floor(value / 289) * 289;
}

function permute(value: number): number {
  return mod289(((value * 34) + 1) * value);
}

function simplexCorner(permutation: number, x: number, y: number): number {
  let magnitude = Math.max(0.5 - x * x - y * y, 0);
  magnitude *= magnitude;
  magnitude *= magnitude;
  const gradientX = 2 * fract(permutation * C_W) - 1;
  const h = Math.abs(gradientX) - 0.5;
  const offsetX = Math.floor(gradientX + 0.5);
  const a0 = gradientX - offsetX;
  magnitude *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);
  return magnitude * (a0 * x + h * y);
}

/** CPU equivalent of the Simplex field used to illuminate the wordmark. */
export function simplexNoise(xInput: number, yInput: number): number {
  let iX = Math.floor(xInput + (xInput + yInput) * C_Y);
  let iY = Math.floor(yInput + (xInput + yInput) * C_Y);
  const x0 = xInput - iX + (iX + iY) * C_X;
  const y0 = yInput - iY + (iX + iY) * C_X;
  const i1X = x0 > y0 ? 1 : 0;
  const i1Y = x0 > y0 ? 0 : 1;
  const x1 = x0 + C_X - i1X;
  const y1 = y0 + C_X - i1Y;
  const x2 = x0 + C_Z;
  const y2 = y0 + C_Z;

  iX = mod289(iX);
  iY = mod289(iY);
  const p0 = permute(permute(iY) + iX);
  const p1 = permute(permute(iY + i1Y) + iX + i1X);
  const p2 = permute(permute(iY + 1) + iX + 1);

  const raw = 130 * (
    simplexCorner(p0, x0, y0)
    + simplexCorner(p1, x1, y1)
    + simplexCorner(p2, x2, y2)
  );
  return raw * 0.5 + 0.5;
}
