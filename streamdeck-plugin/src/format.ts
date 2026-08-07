/** Fixed-width scrolling window over a title; offset advances per poll tick. */
export function marquee(
  text: string,
  offset: number,
  width: number,
  gap = 3,
): string {
  if (text.length <= width) return text;
  const looped = text + " ".repeat(gap);
  const start = offset % looped.length;
  return (looped + looped).slice(start, start + width);
}
