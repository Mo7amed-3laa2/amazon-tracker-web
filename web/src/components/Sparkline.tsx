/** A 12-point trend for a table row: the shape only, in de-emphasis ink, with
 *  the current value marked in the accent hue. Exact numbers come from the row's
 *  own price column and the detail chart, so nothing here is the only route to
 *  a value — it is decoration for the eye, and is hidden from the reader.
 */

const W = 68;
const H = 22;
const POINTS = 12;

export function Sparkline({ values }: { values: number[] }) {
  const tail = values.slice(-POINTS);

  if (tail.length < 2) {
    return <span className="product__meta">—</span>;
  }

  const lo = Math.min(...tail);
  const hi = Math.max(...tail);
  const span = hi - lo || 1;

  const x = (i: number) => (i / (tail.length - 1)) * (W - 4) + 2;
  // Inset by 3px top and bottom so a peak is never clipped by the viewBox.
  const y = (value: number) => H - 3 - ((value - lo) / span) * (H - 6);

  const path = tail.map((value, i) => `${i === 0 ? "M" : "L"}${x(i)} ${y(value)}`).join(" ");

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true" focusable="false">
      <path
        d={path}
        fill="none"
        stroke="var(--axis)"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle
        cx={x(tail.length - 1)}
        cy={y(tail[tail.length - 1])}
        r={2.5}
        fill="var(--series-1)"
      />
    </svg>
  );
}
