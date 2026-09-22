import { useMemo, useRef, useState } from "react";
import { money, parseTimestamp, shortMoney, type PricePoint } from "../api";

/** Price over time: one series, so a line with a wash and no legend — the
 *  heading above it already says what is plotted.
 *
 *  Drawn as plain SVG rather than through a chart library, for two reasons: the
 *  mark specs here (2px line, 8px end marker with a 2px surface ring, hairline
 *  solid gridlines, a crosshair that snaps to the nearest point) are easier to
 *  hit exactly than to argue a library into, and it keeps a charting dependency
 *  off a Pi that has to serve this bundle.
 */

const VIEW_W = 760;
const VIEW_H = 240;
const PAD = { top: 18, right: 78, bottom: 30, left: 62 };

const PLOT_W = VIEW_W - PAD.left - PAD.right;
const PLOT_H = VIEW_H - PAD.top - PAD.bottom;

type Props = {
  points: PricePoint[];
  /** The alert ceiling, drawn as a reference line when one is set. */
  ceiling?: number | null;
  label: string;
};

/** Axis ticks on round numbers — 0 / 1,000 / 2,000 rather than wherever the
 *  data happens to end. */
function niceTicks(min: number, max: number, count: number): number[] {
  if (min === max) {
    // A flat series still needs an axis with room around the line.
    const pad = Math.max(1, Math.abs(min) * 0.05);
    min -= pad;
    max += pad;
  }
  const rawStep = (max - min) / count;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const normalised = rawStep / magnitude;
  let step = (normalised >= 5 ? 5 : normalised >= 2 ? 2 : 1) * magnitude;

  // Snapping the step to 1/2/5 rounds it down as often as up, which on a plot
  // this short can leave a gridline every 27px. Doubling until the count is
  // back in range keeps them recessive without giving up round numbers.
  while ((max - min) / step > count + 2) {
    step *= 2;
  }

  const first = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let value = first; value <= max + step * 0.001; value += step) {
    ticks.push(Math.round(value * 100) / 100);
  }
  return ticks;
}

export function PriceChart({ points, ceiling, label }: Props) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [view, setView] = useState<"chart" | "table">("chart");
  const wrapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const model = useMemo(() => {
    const parsed = points.map((p) => ({ ...p, at: parseTimestamp(p.recorded_at).getTime() }));

    const prices = parsed.map((p) => p.price);
    const candidates = ceiling != null ? [...prices, ceiling] : prices;
    const lo = Math.min(...candidates);
    const hi = Math.max(...candidates);

    // A 6% breathing margin, so the line and the ceiling never sit on the frame.
    const span = hi - lo || Math.max(1, hi * 0.1);
    const yMin = lo - span * 0.06;
    const yMax = hi + span * 0.06;

    const firstAt = parsed[0]?.at ?? 0;
    const lastAt = parsed[parsed.length - 1]?.at ?? 1;
    const timeSpan = lastAt - firstAt || 1;

    // A real time axis, not one point per equal slot: scans are irregular, and
    // evenly spacing them would draw a gap as if it were a normal interval.
    const x = (at: number) =>
      parsed.length === 1 ? PAD.left + PLOT_W / 2 : PAD.left + ((at - firstAt) / timeSpan) * PLOT_W;
    const y = (price: number) =>
      PAD.top + PLOT_H - ((price - yMin) / (yMax - yMin)) * PLOT_H;

    return {
      parsed,
      x,
      y,
      yTicks: niceTicks(yMin, yMax, 4),
      low: Math.min(...prices),
      high: Math.max(...prices),
    };
  }, [points, ceiling]);

  if (points.length === 0) {
    return <p className="empty">No price points recorded yet.</p>;
  }

  const { parsed, x, y, yTicks } = model;
  const last = parsed[parsed.length - 1];

  const linePath = parsed.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.at)} ${y(p.price)}`).join(" ");
  const areaPath =
    parsed.length > 1
      ? `${linePath} L${x(last.at)} ${PAD.top + PLOT_H} L${x(parsed[0].at)} ${PAD.top + PLOT_H} Z`
      : "";

  const active = hovered === null ? null : parsed[hovered];

  /** Snap to the nearest point by x, so the reader aims at a date rather than
   *  at a 2px line. */
  function locate(clientX: number) {
    const svg = svgRef.current;
    if (!svg) return;
    const box = svg.getBoundingClientRect();
    const viewX = ((clientX - box.left) / box.width) * VIEW_W;

    let nearest = 0;
    let best = Infinity;
    parsed.forEach((p, i) => {
      const distance = Math.abs(x(p.at) - viewX);
      if (distance < best) {
        best = distance;
        nearest = i;
      }
    });
    setHovered(nearest);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    const from = hovered === null ? parsed.length - 1 : hovered;
    setHovered(Math.min(parsed.length - 1, Math.max(0, from + step)));
  }

  /** Tooltip position in container pixels, since the SVG scales to fit. */
  const tooltipLeft = () => {
    const svg = svgRef.current;
    const wrap = wrapRef.current;
    if (!svg || !wrap || !active) return 0;
    const ratio = svg.getBoundingClientRect().width / VIEW_W;
    const raw = x(active.at) * ratio;
    // Clamped so it never hangs off either edge of the card.
    return Math.min(Math.max(raw, 66), wrap.clientWidth - 66);
  };

  return (
    <div className="chart" ref={wrapRef}>
      <div className="card__head">
        <span className="card__sub">
          {parsed.length} price point{parsed.length === 1 ? "" : "s"} · low{" "}
          {money(model.low)} · high {money(model.high)}
        </span>
        <div className="segmented" role="group" aria-label="Price history view">
          <button type="button" aria-pressed={view === "chart"} onClick={() => setView("chart")}>
            Chart
          </button>
          <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>
            Table
          </button>
        </div>
      </div>

      {view === "table" ? (
        <table className="table">
          <caption className="sr-only">{label} price history</caption>
          <thead>
            <tr>
              <th>Recorded</th>
              <th className="table__num">Price</th>
            </tr>
          </thead>
          <tbody>
            {[...parsed].reverse().map((p, i) => (
              <tr key={`${p.recorded_at}-${i}`}>
                <td>{parseTimestamp(p.recorded_at).toLocaleString()}</td>
                <td className="table__num">{money(p.price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <>
          <svg
            ref={svgRef}
            className="chart__svg"
            viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
            role="img"
            tabIndex={0}
            aria-label={`${label}: price from ${money(parsed[0].price)} to ${money(last.price)}. Use the table view for exact values.`}
            onPointerMove={(e) => locate(e.clientX)}
            onPointerLeave={() => setHovered(null)}
            onFocus={() => setHovered(parsed.length - 1)}
            onBlur={() => setHovered(null)}
            onKeyDown={onKeyDown}
          >
            {/* Gridlines: hairline, solid, one step off the surface. */}
            {yTicks.map((tick) => (
              <g key={tick}>
                <line
                  x1={PAD.left}
                  x2={PAD.left + PLOT_W}
                  y1={y(tick)}
                  y2={y(tick)}
                  stroke="var(--gridline)"
                  strokeWidth={1}
                />
                <text
                  x={PAD.left - 10}
                  y={y(tick)}
                  textAnchor="end"
                  dominantBaseline="middle"
                  fill="var(--text-muted)"
                  fontSize={11}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {shortMoney(tick)}
                </text>
              </g>
            ))}

            {/* The alert ceiling. Dashed and in muted ink so it reads as a
                target rather than as a second series. */}
            {ceiling != null && (
              <g>
                <line
                  x1={PAD.left}
                  x2={PAD.left + PLOT_W}
                  y1={y(ceiling)}
                  y2={y(ceiling)}
                  stroke="var(--text-muted)"
                  strokeWidth={1}
                  strokeDasharray="5 4"
                />
                {/* Left-anchored and sat above its line, because the right
                    margin belongs to the end-value label — put both there and
                    they collide whenever the ceiling is near the last price. */}
                <text
                  x={PAD.left + 6}
                  y={y(ceiling) - 5}
                  fill="var(--text-secondary)"
                  fontSize={11}
                >
                  alert at {shortMoney(ceiling)}
                </text>
              </g>
            )}

            {areaPath && <path d={areaPath} fill="var(--series-1-wash)" />}

            <path
              d={linePath}
              fill="none"
              stroke="var(--series-1)"
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {/* Crosshair, drawn under the markers so it never covers one. */}
            {active && (
              <line
                x1={x(active.at)}
                x2={x(active.at)}
                y1={PAD.top}
                y2={PAD.top + PLOT_H}
                stroke="var(--axis)"
                strokeWidth={1}
              />
            )}

            {/* A lone point would otherwise be an invisible zero-length line. */}
            {parsed.length === 1 && (
              <circle
                cx={x(parsed[0].at)}
                cy={y(parsed[0].price)}
                r={4}
                fill="var(--series-1)"
                stroke="var(--surface-1)"
                strokeWidth={2}
              />
            )}

            {/* End marker: 8px with a 2px surface ring, so it stays legible
                where it meets the line and the axis. */}
            <circle
              cx={x(last.at)}
              cy={y(last.price)}
              r={4}
              fill="var(--series-1)"
              stroke="var(--surface-1)"
              strokeWidth={2}
            />

            {active && active !== last && (
              <circle
                cx={x(active.at)}
                cy={y(active.price)}
                r={4}
                fill="var(--series-1)"
                stroke="var(--surface-1)"
                strokeWidth={2}
              />
            )}

            {/* The one direct label: the value at the end of the line. */}
            <text
              x={x(last.at) + 10}
              y={y(last.price)}
              dominantBaseline="middle"
              fill="var(--text-primary)"
              fontSize={12}
              fontWeight={600}
            >
              {shortMoney(last.price)}
            </text>

            <line
              x1={PAD.left}
              x2={PAD.left + PLOT_W}
              y1={PAD.top + PLOT_H}
              y2={PAD.top + PLOT_H}
              stroke="var(--axis)"
              strokeWidth={1}
            />

            <text x={PAD.left} y={VIEW_H - 8} fill="var(--text-muted)" fontSize={11}>
              {parseTimestamp(parsed[0].recorded_at).toLocaleDateString()}
            </text>
            {parsed.length > 1 && (
              <text
                x={PAD.left + PLOT_W}
                y={VIEW_H - 8}
                textAnchor="end"
                fill="var(--text-muted)"
                fontSize={11}
              >
                {parseTimestamp(last.recorded_at).toLocaleDateString()}
              </text>
            )}
          </svg>

          {active && (
            <div
              className="chart__tooltip"
              style={{ left: tooltipLeft(), top: 34, transform: "translateX(-50%)" }}
              role="status"
            >
              <div className="chart__tooltip-value">
                <span className="chart__key" />
                {money(active.price)}
              </div>
              <div className="chart__tooltip-when">
                {parseTimestamp(active.recorded_at).toLocaleString()}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
