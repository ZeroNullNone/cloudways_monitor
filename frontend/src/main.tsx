import React, { FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type AuthState = {
  authenticated: boolean;
  username: string | null;
};

function App() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [path, setPath] = useState(window.location.pathname);

  useEffect(() => {
    let active = true;
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then((response) => response.json() as Promise<AuthState>)
      .then((payload) => {
        if (active) {
          setAuth(payload);
        }
      })
      .catch(() => {
        if (active) {
          setAuth({ authenticated: false, username: null });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (auth === null) {
      return;
    }
    if (!auth.authenticated && path !== "/login") {
      window.history.replaceState(null, "", "/login");
      setPath("/login");
    }
    if (auth.authenticated && path === "/login") {
      window.history.replaceState(null, "", "/");
      setPath("/");
    }
  }, [auth, path]);

  if (auth === null) {
    return (
      <main className="shell shell-center">
        <p className="loading">Checking session</p>
      </main>
    );
  }

  if (!auth.authenticated || path === "/login") {
    return <LoginView onAuthenticated={setAuth} setPath={setPath} />;
  }

  return <DashboardShell auth={auth} onAuthenticated={setAuth} setPath={setPath} />;
}

function LoginView({
  onAuthenticated,
  setPath,
}: {
  onAuthenticated: (auth: AuthState) => void;
  setPath: (path: string) => void;
}) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username, password }),
    });
    setSubmitting(false);
    if (!response.ok) {
      setError("Invalid username or password");
      return;
    }
    const payload = (await response.json()) as AuthState;
    onAuthenticated(payload);
    window.history.replaceState(null, "", "/");
    setPath("/");
  }

  return (
    <main className="auth-shell">
      <form className="login-panel" onSubmit={submit}>
        <div>
          <p className="eyebrow">Cloudways Monitor</p>
          <h1>Sign in</h1>
        </div>
        <label>
          Username
          <input
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label>
          Password
          <input
            autoComplete="current-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <button type="submit" disabled={submitting}>
          {submitting ? "Signing in" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

function DashboardShell({
  auth,
  onAuthenticated,
  setPath,
}: {
  auth: AuthState;
  onAuthenticated: (auth: AuthState) => void;
  setPath: (path: string) => void;
}) {
  async function logout() {
    await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    });
    onAuthenticated({ authenticated: false, username: null });
    window.history.replaceState(null, "", "/login");
    setPath("/login");
  }

  return (
    <main className="shell">
      <nav className="topbar">
        <div>
          <p className="eyebrow">Cloudways Monitor</p>
          <h1>Live server and application telemetry</h1>
        </div>
        <div className="account">
          <span>{auth.username}</span>
          <button type="button" onClick={logout}>
            Logout
          </button>
        </div>
      </nav>
      <section className="hero">
        <p className="summary">
          No telemetry panels are available yet.
        </p>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
