import { useEffect, useMemo, useState } from "react";
import { api, money, type PricePoint, type Product } from "../api";
import { PriceChart } from "./PriceChart";
import { Sparkline } from "./Sparkline";

type SortKey = "name" | "last_price" | "change_percent" | "added_at";

/** The tracked-products table: inline controls per row, a chart on expand.
 *
 * Every edit writes straight through to the API and replaces the row from the
 * response, so what is on screen is always what the database holds — no local
 * copy to drift.
 */
export function ProductList({
  products,
  onChanged,
  onRemoved,
}: {
  products: Product[];
  onChanged: (product: Product) => void;
  onRemoved: (id: number) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("added_at");
  const [ascending, setAscending] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sorted = useMemo(() => {
    const direction = ascending ? 1 : -1;
    return [...products].sort((a, b) => {
      const left = a[sortKey];
      const right = b[sortKey];

      // Nulls always sort last, whichever way the column is pointing: a product
      // with no price yet is missing data, not the cheapest thing on the list.
      if (left === null) return 1;
      if (right === null) return -1;

      if (typeof left === "string" && typeof right === "string") {
        return left.localeCompare(right) * direction;
      }
      return ((left as number) - (right as number)) * direction;
    });
  }, [products, sortKey, ascending]);

  function sortBy(key: SortKey) {
    if (key === sortKey) {
      setAscending(!ascending);
    } else {
      setSortKey(key);
      // Text reads naturally A–Z; numbers are usually wanted biggest-first.
      setAscending(key === "name");
    }
  }

  function header(key: SortKey, label: string, numeric = false) {
    const active = sortKey === key;
    return (
      <th
        className={numeric ? "table__num" : undefined}
        aria-sort={active ? (ascending ? "ascending" : "descending") : "none"}
      >
        <button className="btn btn--ghost btn--sm" onClick={() => sortBy(key)}>
          {label}
          {active ? (ascending ? " ↑" : " ↓") : ""}
        </button>
      </th>
    );
  }

  if (products.length === 0) {
    return (
      <div className="card">
        <p className="empty">
          Nothing tracked yet. Paste an Amazon link above to start watching a price.
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card__head">
        <h2 className="card__title">Tracked products</h2>
        <span className="card__sub">
          {products.length} item{products.length === 1 ? "" : "s"}
        </span>
      </div>

      {error && <p className="note note--error">{error}</p>}

      <table className="table">
        <thead>
          <tr>
            {header("name", "Product")}
            {header("last_price", "Price", true)}
            {header("change_percent", "Change", true)}
            <th>Trend</th>
            <th className="table__num">Alert at or below</th>
            <th>Amazon only</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {sorted.map((product) => (
            <ProductRow
              key={product.id}
              product={product}
              expanded={expanded === product.id}
              onToggle={() => setExpanded(expanded === product.id ? null : product.id)}
              onChanged={onChanged}
              onRemoved={onRemoved}
              onError={setError}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProductRow({
  product,
  expanded,
  onToggle,
  onChanged,
  onRemoved,
  onError,
}: {
  product: Product;
  expanded: boolean;
  onToggle: () => void;
  onChanged: (product: Product) => void;
  onRemoved: (id: number) => void;
  onError: (message: string | null) => void;
}) {
  const [ceiling, setCeiling] = useState(
    product.price_max === null ? "" : String(product.price_max),
  );
  const [history, setHistory] = useState<PricePoint[] | null>(null);
  const [busy, setBusy] = useState(false);

  // The sweep can change a threshold underneath an open table, so the input
  // follows the row rather than keeping whatever it was first given.
  useEffect(() => {
    setCeiling(product.price_max === null ? "" : String(product.price_max));
  }, [product.price_max]);

  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    api
      .history(product.id)
      .then((points) => {
        if (!cancelled) setHistory(points);
      })
      .catch((e) => onError(e instanceof Error ? e.message : "Could not load history"));
    return () => {
      cancelled = true;
    };
  }, [expanded, product.id, product.last_price, onError]);

  async function saveCeiling() {
    const trimmed = ceiling.trim();
    const next = trimmed === "" ? null : Number(trimmed);

    if (next !== null && Number.isNaN(next)) {
      onError("That alert price is not a number");
      return;
    }
    if (next === product.price_max) return;

    onError(null);
    try {
      // null is sent deliberately: the API reads an explicit null as "clear
      // this threshold" and an absent key as "leave it alone".
      onChanged(await api.updateProduct(product.id, { price_max: next }));
    } catch (e) {
      onError(e instanceof Error ? e.message : "Could not save that alert price");
      setCeiling(product.price_max === null ? "" : String(product.price_max));
    }
  }

  async function toggleAmazonOnly(value: boolean) {
    onError(null);
    try {
      onChanged(await api.updateProduct(product.id, { require_amazon_merchant: value }));
    } catch (e) {
      onError(e instanceof Error ? e.message : "Could not save that setting");
    }
  }

  async function refresh() {
    setBusy(true);
    onError(null);
    try {
      onChanged(await api.refreshProduct(product.id));
    } catch (e) {
      onError(e instanceof Error ? e.message : "Could not refresh that product");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    const name = product.name ?? "this product";
    if (!window.confirm(`Stop tracking ${name}? Its price history goes too.`)) return;

    setBusy(true);
    onError(null);
    try {
      await api.removeProduct(product.id);
      onRemoved(product.id);
    } catch (e) {
      onError(e instanceof Error ? e.message : "Could not remove that product");
      setBusy(false);
    }
  }

  return (
    <>
      <tr className="table__row">
        <td>
          <div className="product">
            {product.image_url ? (
              <img className="product__thumb" src={product.image_url} alt="" loading="lazy" />
            ) : (
              <span className="product__thumb" aria-hidden="true" />
            )}
            <span>
              <a
                className="product__name"
                href={product.url}
                target="_blank"
                rel="noreferrer noopener"
              >
                {product.name ?? product.url}
              </a>
              <span className="product__meta">
                {product.is_amazon ? "Amazon" : (product.merchant_name ?? "Other seller")}
                {!product.available && (
                  <>
                    {" · "}
                    <span className="tag tag--out">out of stock</span>
                  </>
                )}
              </span>
            </span>
          </div>
        </td>

        <td className="table__num">
          <span className="price">{money(product.last_price)}</span>
        </td>

        <td className="table__num">
          <Delta product={product} />
        </td>

        <td>
          <Sparkline values={product.trend} />
        </td>

        <td className="table__num">
          <input
            className="input input--num"
            type="number"
            min={0}
            step="0.01"
            value={ceiling}
            placeholder="any drop"
            aria-label={`Alert price for ${product.name ?? "this product"}`}
            onChange={(e) => setCeiling(e.target.value)}
            onBlur={saveCeiling}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") {
                setCeiling(product.price_max === null ? "" : String(product.price_max));
                e.currentTarget.blur();
              }
            }}
          />
        </td>

        <td>
          <label className="check">
            <input
              type="checkbox"
              checked={product.require_amazon_merchant}
              onChange={(e) => toggleAmazonOnly(e.target.checked)}
              aria-label={`Only alert when Amazon is the seller of ${product.name ?? "this product"}`}
            />
          </label>
        </td>

        <td>
          <div className="actions">
            <button
              className="btn btn--ghost btn--sm"
              onClick={onToggle}
              aria-expanded={expanded}
            >
              {expanded ? "Hide history" : "History"}
            </button>
            <button className="btn btn--ghost btn--sm" onClick={refresh} disabled={busy}>
              {busy ? "…" : "Refresh"}
            </button>
            <button
              className="btn btn--ghost btn--danger btn--sm"
              onClick={remove}
              disabled={busy}
            >
              Remove
            </button>
          </div>
        </td>
      </tr>

      {expanded && (
        <tr>
          <td colSpan={7} className="detail">
            {history === null ? (
              <p className="empty">Loading history…</p>
            ) : (
              <>
                <div className="detail__grid">
                  <Tile label="Current price" value={money(product.last_price)}>
                    <Delta product={product} />
                  </Tile>
                  <Tile
                    label="Previous price"
                    value={money(product.previous_price)}
                  />
                  <Tile
                    label="Alert price"
                    value={product.price_max === null ? "Any drop" : money(product.price_max)}
                  />
                </div>
                <PriceChart
                  points={history}
                  ceiling={product.price_max}
                  label={product.name ?? "This product"}
                />
              </>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function Tile({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children?: React.ReactNode;
}) {
  return (
    <div>
      <div className="tile__label">{label}</div>
      <div className="tile__value">{value}</div>
      {children && <div className="tile__delta">{children}</div>}
    </div>
  );
}

/** Change since the previous scan.
 *
 * The arrow and the sign carry the direction as well as the colour, so the
 * reading survives colour blindness and a greyscale print.
 */
function Delta({ product }: { product: Product }) {
  const { change, change_percent: percent } = product;

  if (change === null || percent === null || change === 0) {
    return <span className="delta--flat">—</span>;
  }

  const dropped = change < 0;
  return (
    <span className={dropped ? "delta--down" : "delta--up"}>
      {dropped ? "↓" : "↑"} {money(Math.abs(change)).replace("EGP ", "")} (
      {percent > 0 ? "+" : ""}
      {percent.toFixed(1)}%)
    </span>
  );
}
