import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

function App() {
  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">Cloudways Monitor</p>
        <h1>Live server and application telemetry</h1>
        <p className="summary">
          Backend scaffolding is ready. The next implementation issues will wire
          Cloudways collection, alerts, and overview panels into this app shell.
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
