interface Props {
  count?: number;
}

export function TrackRowSkeleton({ count = 5 }: Props) {
  return (
    <ul className="p-1 space-y-0.5">
      {Array.from({ length: count }).map((_, i) => (
        <li
          key={i}
          className="flex items-center gap-2 rounded-md p-2 animate-pulse"
        >
          <div className="h-7 w-7 rounded bg-muted shrink-0" />
          <div className="flex-1 min-w-0 space-y-1.5">
            <div className="h-3 w-3/4 bg-muted rounded" />
            <div className="h-2.5 w-1/2 bg-muted/60 rounded" />
          </div>
          <div className="h-7 w-7 rounded bg-muted shrink-0" />
        </li>
      ))}
    </ul>
  );
}
