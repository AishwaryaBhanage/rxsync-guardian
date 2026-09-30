import { useEffect, useRef, useState } from "react";
import { getHealth, hasKey } from "./api";
import { EvalTab } from "./components/EvalTab";
import { InvestigateTab } from "./components/InvestigateTab";

const TABS = [
  { id: "investigate", label: "Investigate" },
  { id: "evals", label: "Eval results" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<TabId>("investigate");
  const [online, setOnline] = useState<boolean | null>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    getHealth()
      .then(() => setOnline(true))
      .catch(() => setOnline(false));
  }, []);

  /* Arrow keys move between tabs, which is what the WAI-ARIA tabs pattern
     expects and what anyone driving this by keyboard will try. */
  function onTabKey(event: React.KeyboardEvent, index: number) {
    const last = TABS.length - 1;
    let next: number | null = null;
    if (event.key === "ArrowRight") next = index === last ? 0 : index + 1;
    if (event.key === "ArrowLeft") next = index === 0 ? last : index - 1;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = last;
    if (next === null) return;
    event.preventDefault();
    setTab(TABS[next].id);
    tabRefs.current[next]?.focus();
  }

  return (
    <>
      <a className="skip" href="#main">
        Skip to main content
      </a>

      <header className="topbar">
        <div className="brand">
          <span className="mark" aria-hidden="true">
            Rx
          </span>
          <h1>RxSync Investigator</h1>
        </div>

        <div className="tabs" role="tablist" aria-label="Sections">
          {TABS.map((item, index) => (
            <button
              key={item.id}
              ref={(node) => {
                tabRefs.current[index] = node;
              }}
              type="button"
              role="tab"
              id={`tab-${item.id}`}
              className="tab"
              aria-selected={tab === item.id}
              aria-controls={`panel-${item.id}`}
              tabIndex={tab === item.id ? 0 : -1}
              onClick={() => setTab(item.id)}
              onKeyDown={(event) => onTabKey(event, index)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <span className="spacer" />

        {online !== null && (
          <span className={online ? "badge-live" : "badge-live is-down"}>
            <span className="dot" aria-hidden="true" />
            {online ? "API connected" : "API unreachable"}
          </span>
        )}
      </header>

      {(!hasKey() || online === false) && (
        <div className="alerts">
          {!hasKey() && (
            <div className="callout callout-error" role="alert">
              <span className="ico" aria-hidden="true">
                !
              </span>
              <span>
                <strong>VITE_DEMO_KEY is not set.</strong> Copy{" "}
                <code>web/.env.local.example</code> to <code>web/.env.local</code>, then restart the
                dev server. Every request will return 401 until you do.
              </span>
            </div>
          )}
          {online === false && hasKey() && (
            <div className="callout callout-error" role="alert">
              <span className="ico" aria-hidden="true">
                !
              </span>
              <span>
                <strong>Cannot reach the API.</strong> Start it with{" "}
                <code>uv run uvicorn api.local:app --reload --port 8000</code> in the repo root.
              </span>
            </div>
          )}
        </div>
      )}

      <main id="main">
        {TABS.map((item) => (
          <div
            key={item.id}
            role="tabpanel"
            id={`panel-${item.id}`}
            aria-labelledby={`tab-${item.id}`}
            hidden={tab !== item.id}
            tabIndex={0}
          >
            {/* Mounted only when visible so a hidden tab does not fetch. */}
            {tab === item.id && (item.id === "investigate" ? <InvestigateTab /> : <EvalTab />)}
          </div>
        ))}
      </main>
    </>
  );
}
