# Browser acceptance

The preview is isolated from deployment configuration. It constructs two synthetic DigitalOcean servers and four applications using an in-process HTTP transport. It never loads `.env`, calls Cloudways, or sends Telegram messages. Fixture credentials are public test values.

## Start

Install the development dependencies as described in [README.md](../README.md), then build the frontend:

```powershell
cd frontend
npm ci
npm run build
cd ..
.venv\Scripts\python.exe -m scripts.e2e_preview
```

Open `http://127.0.0.1:8084` and sign in with `admin` / `correct-password`. Run acceptance within ten minutes of startup so fixture timestamps remain fresh. Stop the server with Ctrl+C after review. Its temporary SQLite database is created under `.tmp/`.

If the real-data preview already occupies 8084, use `.venv\Scripts\python.exe -m uvicorn scripts.e2e_preview:build_preview --factory --host 127.0.0.1 --port 8085` and open 8085 for fixture acceptance.

## Automated browser check

Use the supported Codex Browser skill runtime. Read its runtime documentation and initialize the browser/tab before running the exported helper. The tab must be signed in to the preview and on its initial dashboard, with no search or changed selections. The helper uses supported browser locators, screenshots and read-only DOM inspection, and restores the viewport afterward.

In the Browser Node REPL, with `browser` and `tab` already initialized:

```javascript
const acceptance = await import('C:/Users/PC/Documents/Code/terminallab/cloudways_monitor/scripts/browser_acceptance.mjs');
await acceptance.runAcceptance(
  browser,
  tab,
  'C:/Users/PC/Documents/Code/terminallab/cloudways_monitor/.tmp/e2e'
);
```

The helper tests a 1280×900 desktop viewport and a 390×844 mobile viewport. Native scrollbars reduce the document width; assertions use its actual client width.

## Assertions and manual review

1. Both servers initially expanded.
2. All 15 fixture DigitalOcean targets available.
3. All six fixture durations available.
4. Trailing unfinished null excluded; latest completed graph summary shown.
5. Latest is visually larger than the secondary statistics.
6. Sample table and both coverage/count footnotes are absent.
7. Latest completed free-memory sample shown with source time.
8. Configured RAM/data disk appear beside free metrics.
9. Total capacity labels remain small and gray.
10. Actual zero remains visible.
11. Desktop graph/app panes form two columns without page overflow.
12. Target selection updates the chart.
13. Six-month selection updates the chart.
14. Collapse all hides graph controls.
15. Selections survive collapse/expand.
16. Application search retains its parent and filters unrelated applications.
17. Returned request counts sort largest first.
18. All four applications have their correctly matched disk usage.
19. Partial requests and unavailable bandwidth are labelled.
20. Opening an application shows HTTP status analysis inline.
21. Top URLs load within that application.
22. A genuine empty slow-page table is clearly labelled.
23. Slow SQL queries display inline.
24. Application analysis period switches independently of the server graph.
25. Collapsing a server removes its application polling components.
26. Mobile panes stack without page overflow.
27. Expanded application analysis stays within the mobile viewport.
28. Interface text is English and time labels show UTC+8.
29. Browser console has no errors.

Visually inspect chart gaps, long-range dates, readable contrast, keyboard focus, both server groups, blank null application cells and real zero cells. The fixed fixture is a contract demonstration, not evidence about an actual Cloudways account's response shape.

The helper writes `desktop.png`, `desktop-full.png`, `mobile.png`, `desktop-dom.txt`, and `browser-results.json`. Committed review artifacts from the implementation run are in [verification/](verification/).
