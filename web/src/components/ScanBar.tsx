import { useEffect, useState } from "react";
import { api, whenShort, type ScanStatus, type Settings } from "../api";

/** The sweep's status, a way to start one, and the interval control.
 *
 * Polls every two seconds while a sweep is running and every thirty otherwise:
 * a sweep takes minutes and the endpoint is cheap, but there is nothing worth
 * watching closely between them.
 */
export function ScanBar({
  isAdmin,
  onSweepFinished,
}: {
  isAdmin: boolean;
  onSweepFinished: () => void;
}) {
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [interval, setIntervalValue] = useState("");
  const [showLog, setShowLog] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const next = await api.scanStatus();
        if (cancelled) return;

        setStatus((previous) => {
          // A sweep that has just stopped is what tells the product table its
          // prices are stale, so the reload fires on that edge only.
          if (previous?.running && !next.running) {
            onSweepFinished();
          }
          return next;
        });

        timer = window.setTimeout(poll, next.running ? 2000 : 30000);
      } catch {
        // A failed poll is not worth a banner — the next one either works, or
        // the session has gone and the next real action will say so.
        if (!cancelled) timer = window.setTimeout(poll, 30000);
      }
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [onSweepFinished]);

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setSettings(s);
        setIntervalValue(String(s.check_interval_minutes));
      })
      .catch(() => setSettings(null));
  }, []);

  async function runNow() {
    setError(null);
    try {
      await api.runScan();
      setStatus((previous) => (previous ? { ...previous, running: true } : previous));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start a scan");
    }
  }

  async function saveInterval(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSaved(false);
    try {
      const updated = await api.setInterval(Number(interval));
      setSettings(updated);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the interval");
    }
  }

  return (
    <div className="card">
      <div className="strip">
        <span className={`dot ${status?.running ? "dot--live" : ""}`} aria-hidden="true" />

        <div className="strip__stat">
          <span className="strip__label">Scan</span>
          <span className="strip__value">
            {status?.running
              ? "Running now"
              : `Last ran ${whenShort(status?.finished_at ?? null)}`}
          </span>
        </div>

        <div className="strip__stat">
          <span className="strip__label">Next</span>
          <span className="strip__value">{whenShort(status?.next_run_at ?? null)}</span>
        </div>

        {status && (status.products_checked > 0 || status.finished_at) && (
          <div className="strip__stat">
            <span className="strip__label">Last result</span>
            <span className="strip__value">
              {status.products_changed} changed of {status.products_checked}
              {status.fetch_failures > 0 && ` · ${status.fetch_failures} failed`}
            </span>
          </div>
        )}

        <div className="strip" style={{ marginLeft: "auto", gap: 10 }}>
          {status && status.log.length > 0 && (
            <button className="btn btn--ghost btn--sm" onClick={() => setShowLog(!showLog)}>
              {showLog ? "Hide log" : "Show log"}
            </button>
          )}
          <button className="btn btn--sm" onClick={runNow} disabled={status?.running}>
            {status?.running ? "Scanning…" : "Scan now"}
          </button>
        </div>
      </div>

      {isAdmin && settings && (
        <form className="row" style={{ marginTop: 14 }} onSubmit={saveInterval}>
          <div className="field">
            <label className="field__label" htmlFor="interval">
              Check every (minutes)
            </label>
            <input
              id="interval"
              className="input input--num"
              type="number"
              value={interval}
              min={settings.min_interval_minutes}
              max={settings.max_interval_minutes}
              onChange={(e) => setIntervalValue(e.target.value)}
            />
          </div>
          <button className="btn btn--sm" type="submit">
            Save
          </button>
          <span className="field__label" style={{ paddingBottom: 8 }}>
            {settings.min_interval_minutes}–{settings.max_interval_minutes}. A sweep takes
            minutes, so anything shorter would overlap itself.
          </span>
          {saved && (
            <span
              className="field__label"
              style={{ paddingBottom: 8, color: "var(--good-text)" }}
            >
              Saved
            </span>
          )}
        </form>
      )}

      {status?.last_error && (
        <p className="note note--error">Last scan failed: {status.last_error}</p>
      )}
      {error && <p className="note note--error">{error}</p>}

      {showLog && status && <pre className="log">{[...status.log].reverse().join("\n")}</pre>}
    </div>
  );
}
