import { useEffect, useState } from "react";
import { getReport } from "../api";
import type { EvalRun, EvalVersion } from "../types";

const percent = (value: number | null) => (value === null ? "—" : `${(value * 100).toFixed(1)}%`);

interface RunRef {
  version: string;
  label: string;
}

/* The two runs the headline compares: both from v2.1, so they are scored on
   the same 48 tickets. */
const HEADLINE: { agent: RunRef; baseline: RunRef } = {
  agent: { version: "v2.1", label: "haiku+tools" },
  baseline: { version: "v2.1", label: "haiku-no-tools" },
};

// Kept in the table for the story, but shown muted: the wording gave the answer away.
const SUPERSEDED = new Set(["v1"]);

const isRun = (version: EvalVersion, run: EvalRun, ref: RunRef) =>
  version.version === ref.version && run.label === ref.label;

function findRun(versions: EvalVersion[], ref: RunRef): EvalRun | undefined {
  return versions
    .find((version) => version.version === ref.version)
    ?.runs.find((run) => run.label === ref.label);
}

/** The newest report date, as YYYY-MM-DD. ISO timestamps sort as strings. */
function latest(dates: (string | null)[]): string | null {
  const known = dates.filter((date): date is string => Boolean(date)).sort();
  return known.length ? known[known.length - 1].slice(0, 10) : null;
}

export function EvalTab() {
  const [versions, setVersions] = useState<EvalVersion[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getReport()
      .then(setVersions)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  if (error) {
    return (
      <div className="callout callout-error" role="alert">
        <span className="ico" aria-hidden="true">
          !
        </span>
        <span>
          <strong>Could not load the eval reports.</strong> {error}
        </span>
      </div>
    );
  }

  if (versions.length === 0) {
    return (
      <div className="card working" aria-busy="true">
        <div className="skeleton sk-title" />
        <div className="skeleton sk-block" />
        <span className="sr-only">Loading eval results…</span>
      </div>
    );
  }

  /* The headline compares two named runs, never a maximum across versions:
     v1's text-only baseline scored 100% only because the wording leaked the
     category, so any "best of" would surface a meaningless number. */
  const agent = findRun(versions, HEADLINE.agent);
  const baseline = findRun(versions, HEADLINE.baseline);
  const lastRun = latest(versions.map((version) => version.generated_at));

  return (
    <div className="work work-center">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="eyebrow">Neutral wording: agent vs text-only baseline</span>
        {lastRun && <span className="last-run">last eval run: {lastRun}</span>}
      </div>

      <div className="stat-row">
        <div className="card stat good">
          <span className="big">{agent ? percent(agent.category_accuracy) : "—"}</span>
          <span className="cap">category accuracy · Haiku 4.5 + tools (v2.1)</span>
          {agent && (
            <span className="sub">
              {percent(agent.rx_accuracy)} rx accuracy · ${agent.cost_per_ticket_usd.toFixed(4)}
              /ticket
            </span>
          )}
        </div>
        <div className="card stat bad">
          <span className="big">{baseline ? percent(baseline.category_accuracy) : "—"}</span>
          <span className="cap">category accuracy · text only, no tools (v2.1)</span>
          {baseline && <span className="sub">{percent(baseline.rx_accuracy)} rx accuracy</span>}
        </div>
      </div>

      <div className="card">
        <span className="eyebrow">Accuracy by ticket-set version</span>
        {/* Outside the scroll box so it wraps on a phone instead of scrolling away. */}
        <p className="note" id="eval-table-note">
          Every version grades 48 tickets against the simulator's answer key. v2.1 changed the
          patients on 7 of the 8 no-issue tickets; the 40 fault tickets are the same throughout.
        </p>
        <div className="table-scroll">
          <table className="eval" aria-describedby="eval-table-note">
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">Run</th>
                <th scope="col">Tools</th>
                <th scope="col">Tickets</th>
                <th scope="col">Category accuracy</th>
                <th scope="col">Rx accuracy</th>
                <th scope="col">Cost / ticket</th>
              </tr>
            </thead>
            <tbody>
              {versions.flatMap((version) =>
                version.runs.map((run, index) => (
                  <tr
                    key={`${version.version}-${run.label}`}
                    className={SUPERSEDED.has(version.version) ? "superseded" : undefined}
                  >
                    {index === 0 && (
                      <th scope="row" rowSpan={version.runs.length}>
                        {version.version}
                        {SUPERSEDED.has(version.version) && (
                          <span className="tag-superseded">
                            superseded: wording leaked the category
                          </span>
                        )}
                      </th>
                    )}
                    <td>{run.label}</td>
                    <td>
                      <span className={run.tools_enabled ? "pill on" : "pill"}>
                        {run.tools_enabled ? "tools" : "text only"}
                      </span>
                    </td>
                    <td>{run.tickets}</td>
                    <td
                      className={
                        isRun(version, run, HEADLINE.agent) ? "best" : undefined
                      }
                    >
                      {percent(run.category_accuracy)}
                    </td>
                    <td>
                      {percent(run.rx_accuracy)}{" "}
                      <span className="muted">({run.rx_scored} scored)</span>
                    </td>
                    <td>${run.cost_per_ticket_usd.toFixed(4)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
        <p className="note">
          v1 wording gave the category away; v2 used neutral wording; v2.1 gave the tools a clock.
        </p>
      </div>

      <div className="card">
        <span className="eyebrow">What changed between versions</span>
        <ul className="version-notes">
          {versions.map((version) => (
            <li key={version.version}>
              <strong>{version.version}</strong> — {version.note}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
