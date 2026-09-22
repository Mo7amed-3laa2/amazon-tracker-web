import { useState } from "react";
import { api, type Account } from "../api";

/** Sign in, or create the first account.
 *
 * Which of the two it offers comes from /auth/bootstrap: a fresh install has no
 * accounts and no way to make one through a login form, so it shows the create
 * form instead. After that, new accounts are added by an admin from the API, not
 * from here.
 */
export function SignIn({
  needsFirstAccount,
  onSignedIn,
}: {
  needsFirstAccount: boolean;
  onSignedIn: (account: Account) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const account = needsFirstAccount
        ? await api.register(username, password)
        : await api.login(username, password);
      onSignedIn(account);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="shell">
      <form className="card signin" onSubmit={submit}>
        <h1 className="signin__title">
          {needsFirstAccount ? "Create your account" : "Price Tracker"}
        </h1>
        <p className="signin__sub">
          {needsFirstAccount
            ? "Nobody has signed up yet, so this first account becomes the admin."
            : "Sign in to see your tracked products."}
        </p>

        <div className="field">
          <label className="field__label" htmlFor="username">
            Username
          </label>
          <input
            id="username"
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
        </div>

        <div className="field">
          <label className="field__label" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={needsFirstAccount ? "new-password" : "current-password"}
            minLength={needsFirstAccount ? 8 : undefined}
            required
          />
          {needsFirstAccount && (
            <span className="field__label">At least 8 characters.</span>
          )}
        </div>

        <button className="btn btn--primary" type="submit" disabled={busy}>
          {busy ? "Working…" : needsFirstAccount ? "Create account" : "Sign in"}
        </button>

        {error && <p className="note note--error">{error}</p>}
      </form>
    </div>
  );
}
