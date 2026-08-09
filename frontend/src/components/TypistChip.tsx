interface Props {
  typist: { name: string; color: string } | null;
}

/**
 * Who is currently typing in a shared field.
 *
 * The dot colour is the point, exactly as in PresenceBar: it is the only thing
 * tying the name here to the cursor moving around the page, so it is an
 * explicit inline colour rather than a utility class whose value could drift
 * away from colorForUid().
 */
export function TypistChip({ typist }: Props) {
  if (!typist) return null;

  return (
    <span className="flex shrink-0 items-center gap-1 rounded-full border bg-muted/50 px-2 py-0.5 text-[10px] text-muted-foreground">
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: typist.color }}
      />
      {/* One text node, so the label reads as one string to a screen reader
          (and to getByText). */}
      {`${typist.name} is typing`}
    </span>
  );
}
