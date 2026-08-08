/** Wrap artwork in a square SVG so a 16:9 thumbnail is letterboxed instead of
 *  stretched. setImage accepts an SVG string, so this needs no image library
 *  and no native dependency. */
const SIZE = 144; // Stream Deck @2x key
const BG = "#1a1a2e";

export function letterboxSvg(dataUri: string): string {
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${SIZE}" height="${SIZE}" ` +
    `viewBox="0 0 ${SIZE} ${SIZE}">` +
    `<rect width="${SIZE}" height="${SIZE}" fill="${BG}"/>` +
    `<image href="${dataUri}" width="${SIZE}" height="${SIZE}" ` +
    `preserveAspectRatio="xMidYMid meet"/>` +
    `</svg>`
  );
}
