import { useState } from "react";
import { api, type Product } from "../api";

/** Paste a link, optionally set an alert price, track it.
 *
 * The submit is slow by nature — the scraper warms a session and retries, which
 * can take most of a minute — so the button states that up front rather than
 * looking hung.
 */
export function AddProduct({ onAdded }: { onAdded: (product: Product) => void }) {
  const [url, setUrl] = useState("");
  const [ceiling, setCeiling] = useState("");
  const [amazonOnly, setAmazonOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setAdded(null);
    try {
      const product = await api.addProduct({
        url: url.trim(),
        price_max: ceiling.trim() === "" ? null : Number(ceiling),
        require_amazon_merchant: amazonOnly,
      });
      onAdded(product);
      setUrl("");
      setCeiling("");
      setAmazonOnly(false);
      setAdded(product.name ?? "Product added");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add that product");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="card__head">
        <h2 className="card__title">Track a product</h2>
        <span className="card__sub">
          Any Amazon product link, including a share link.
        </span>
      </div>

      <form className="row" onSubmit={submit}>
        <div className="field" style={{ flex: "1 1 320px" }}>
          <label className="field__label" htmlFor="url">
            Product URL
          </label>
          <input
            id="url"
            className="input"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.amazon.eg/dp/…"
            required
          />
        </div>

        <div className="field">
          <label className="field__label" htmlFor="ceiling">
            Alert at or below
          </label>
          <input
            id="ceiling"
            className="input input--num"
            type="number"
            min={0}
            step="0.01"
            value={ceiling}
            onChange={(e) => setCeiling(e.target.value)}
            placeholder="any drop"
          />
        </div>

        <label className="check" style={{ paddingBottom: 9 }}>
          <input
            type="checkbox"
            checked={amazonOnly}
            onChange={(e) => setAmazonOnly(e.target.checked)}
          />
          Amazon as seller only
        </label>

        <button className="btn btn--primary" type="submit" disabled={busy}>
          {busy ? "Fetching…" : "Track"}
        </button>
      </form>

      {busy && (
        <p className="note">
          Fetching the page. Amazon is slow to answer a cold session, so this can take
          up to a minute.
        </p>
      )}
      {error && <p className="note note--error">{error}</p>}
      {added && !busy && <p className="note note--ok">Now tracking {added}.</p>}
    </div>
  );
}
