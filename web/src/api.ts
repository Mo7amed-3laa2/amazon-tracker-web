/** The typed client for the FastAPI app.
 *
 * Every call is same-origin — the built frontend is served by FastAPI, and in
 * development Vite proxies /api — so the session cookie rides along without any
 * CORS handling here. `credentials: "same-origin"` is still explicit, because
 * the default varies between browsers for cross-origin cases.
 */

export type Account = {
  user_id: number;
  username: string;
  language: "en" | "ar";
  is_admin: boolean;
};

export type Product = {
  id: number;
  url: string;
  name: string | null;
  last_price: number | null;
  previous_price: number | null;
  image_url: string | null;
  added_at: string;
  merchant_name: string | null;
  is_amazon: boolean;
  alert_threshold: number | null;
  price_min: number | null;
  price_max: number | null;
  require_amazon_merchant: boolean;
  available: boolean;
  change: number | null;
  change_percent: number | null;
  /** Up to 12 prices, oldest first, for the row sparkline. */
  trend: number[];
};

export type PricePoint = {
  price: number;
  recorded_at: string;
};

export type ScanStatus = {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  products_checked: number;
  products_changed: number;
  alerts_matched: number;
  fetch_failures: number;
  last_error: string | null;
  log: string[];
  next_run_at: string | null;
};

export type Settings = {
  check_interval_minutes: number;
  min_interval_minutes: number;
  max_interval_minutes: number;
  next_run_at: string | null;
  applied_now?: boolean;
};

/** An API error carrying the status, so callers can tell 401 from a real fault. */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "same-origin",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const body = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(body, response.status));
  }

  return body as T;
}

/** Turn FastAPI's error shapes into one line a person can read.
 *
 * `detail` is a string for our own HTTPExceptions but a list of per-field
 * objects for a validation failure, which would otherwise render as
 * "[object Object]" in the UI.
 */
function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;

  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: string; loc?: unknown[] } | undefined;
    if (first?.msg) {
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : null;
      const cleaned = first.msg.replace(/^Value error, /, "");
      return field ? `${field}: ${cleaned}` : cleaned;
    }
  }

  if (status === 401) return "Your session has expired. Sign in again.";
  return `Request failed (${status})`;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  bootstrap: () => request<{ needs_first_account: boolean }>("/auth/bootstrap"),

  me: () => request<Account>("/auth/me"),

  login: (username: string, password: string) =>
    request<Account>("/auth/login", { method: "POST", body: json({ username, password }) }),

  register: (username: string, password: string) =>
    request<Account>("/auth/register", { method: "POST", body: json({ username, password }) }),

  logout: () => request<void>("/auth/logout", { method: "POST" }),

  setLanguage: (language: "en" | "ar") =>
    request<Account>("/auth/me/language", { method: "PUT", body: json({ language }) }),

  products: () => request<Product[]>("/products"),

  addProduct: (body: {
    url: string;
    price_min?: number | null;
    price_max?: number | null;
    require_amazon_merchant?: boolean;
  }) => request<Product>("/products", { method: "POST", body: json(body) }),

  /** Partial update. Only the keys passed are sent, so an explicit null clears a
   *  threshold while an omitted key leaves it alone — the API relies on that. */
  updateProduct: (
    id: number,
    patch: Partial<Pick<Product, "price_min" | "price_max" | "require_amazon_merchant">>,
  ) => request<Product>(`/products/${id}`, { method: "PATCH", body: json(patch) }),

  removeProduct: (id: number) => request<void>(`/products/${id}`, { method: "DELETE" }),

  history: (id: number, limit = 90) =>
    request<PricePoint[]>(`/products/${id}/history?limit=${limit}`),

  refreshProduct: (id: number) => request<Product>(`/products/${id}/refresh`, { method: "POST" }),

  scanStatus: () => request<ScanStatus>("/scan/status"),

  runScan: () => request<{ started: boolean }>("/scan/run", { method: "POST" }),

  settings: () => request<Settings>("/settings"),

  setInterval: (minutes: number) =>
    request<Settings>("/settings", {
      method: "PUT",
      body: json({ check_interval_minutes: minutes }),
    }),
};

/** EGP with no fractional noise on whole amounts, and grouped thousands. */
export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `EGP ${value.toLocaleString("en-EG", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function shortMoney(value: number): string {
  return value.toLocaleString("en-EG", { maximumFractionDigits: 0 });
}

/** SQLite stores CURRENT_TIMESTAMP as "YYYY-MM-DD HH:MM:SS" in UTC with no
 *  zone marker, which browsers parse as local time. Normalising it here is what
 *  keeps chart timestamps from drifting by the timezone offset. */
export function parseTimestamp(raw: string): Date {
  const iso = raw.includes("T") ? raw : raw.replace(" ", "T");
  return new Date(/[Z+]/.test(iso) ? iso : `${iso}Z`);
}

export function whenShort(raw: string | null): string {
  if (!raw) return "—";
  return parseTimestamp(raw).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
