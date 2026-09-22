import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type Account, type Product } from "./api";
import { AddProduct } from "./components/AddProduct";
import { ProductList } from "./components/ProductList";
import { ScanBar } from "./components/ScanBar";
import { SignIn } from "./components/SignIn";

type Theme = "light" | "dark" | "system";

const THEME_KEY = "tracker-theme";

/** Remembered per browser, so it is a convenience rather than account state —
 *  a cleared or blocked store just means the OS setting wins. */
function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // Private windows and blocked site data both throw here.
  }
  return "system";
}

export function App() {
  const [account, setAccount] = useState<Account | null>(null);
  const [needsFirstAccount, setNeedsFirstAccount] = useState(false);
  const [checking, setChecking] = useState(true);
  const [products, setProducts] = useState<Product[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", theme);
    }
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // Not worth surfacing: the theme still applies for this session.
    }
  }, [theme]);

  // Whether there is a live session, and if not, whether this install has any
  // accounts at all — which decides between the sign-in and the create form.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.me();
        if (!cancelled) setAccount(me);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) {
          try {
            const { needs_first_account } = await api.bootstrap();
            if (!cancelled) setNeedsFirstAccount(needs_first_account);
          } catch {
            // Leave it on the sign-in form; the attempt will report the fault.
          }
        }
      } finally {
        if (!cancelled) setChecking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadProducts = useCallback(async () => {
    try {
      setProducts(await api.products());
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setAccount(null);
        return;
      }
      setError(e instanceof Error ? e.message : "Could not load your products");
    }
  }, []);

  useEffect(() => {
    if (account) loadProducts();
  }, [account, loadProducts]);

  async function signOut() {
    await api.logout().catch(() => undefined);
    setAccount(null);
    setProducts([]);
  }

  const replaceProduct = useCallback((updated: Product) => {
    setProducts((current) =>
      current.some((p) => p.id === updated.id)
        ? current.map((p) => (p.id === updated.id ? updated : p))
        : [updated, ...current],
    );
  }, []);

  const dropProduct = useCallback((id: number) => {
    setProducts((current) => current.filter((p) => p.id !== id));
  }, []);

  if (checking) {
    return (
      <div className="shell">
        <p className="empty">Loading…</p>
      </div>
    );
  }

  if (!account) {
    return (
      <SignIn
        needsFirstAccount={needsFirstAccount}
        onSignedIn={(signedIn) => {
          setAccount(signedIn);
          setNeedsFirstAccount(false);
        }}
      />
    );
  }

  return (
    <div className="shell">
      <header className="topbar">
        <h1 className="topbar__title">Price Tracker</h1>
        <span className="topbar__who">
          {account.username}
          {account.is_admin && " · admin"}
        </span>
        <div className="segmented" role="group" aria-label="Theme">
          {(["light", "system", "dark"] as Theme[]).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={theme === option}
              onClick={() => setTheme(option)}
            >
              {option === "light" ? "Light" : option === "dark" ? "Dark" : "Auto"}
            </button>
          ))}
        </div>
        <button className="btn btn--sm" onClick={signOut}>
          Sign out
        </button>
      </header>

      <ScanBar isAdmin={account.is_admin} onSweepFinished={loadProducts} />
      <AddProduct onAdded={replaceProduct} />

      {error && <p className="note note--error">{error}</p>}

      <div style={{ marginTop: 16 }}>
        <ProductList
          products={products}
          onChanged={replaceProduct}
          onRemoved={dropProduct}
        />
      </div>
    </div>
  );
}
