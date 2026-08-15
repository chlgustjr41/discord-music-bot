/** Wrap artwork in a square SVG so a 16:9 thumbnail is letterboxed instead of
 *  stretched, then hand it over as a base64 data URI.
 *
 *  A data URI and not a raw `<svg>` string, although the SDK docs list raw
 *  SVG as a supported setImage format: Stream Deck 7.x on Windows (7.4.2
 *  verified) silently ignores raw SVG strings — the key keeps its manifest
 *  glyph, nothing errors client- or host-side, and the plugin log happily
 *  says "applying artwork". Base64 rather than utf8+percent-encoding so the
 *  embedded JPEG data URI (full of `+` and `/`) never meets an escaping
 *  edge case. */
const SIZE = 144; // Stream Deck @2x key
const BG = "#1a1a2e";

export function letterboxSvg(dataUri: string): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${SIZE}" height="${SIZE}" ` +
    `viewBox="0 0 ${SIZE} ${SIZE}">` +
    `<rect width="${SIZE}" height="${SIZE}" fill="${BG}"/>` +
    `<image href="${dataUri}" width="${SIZE}" height="${SIZE}" ` +
    `preserveAspectRatio="xMidYMid meet"/>` +
    `</svg>`;
  return `data:image/svg+xml;base64,${Buffer.from(svg, "utf8").toString("base64")}`;
}
