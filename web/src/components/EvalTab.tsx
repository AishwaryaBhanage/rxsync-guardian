import { useEffect, useState } from "react";
import { getReport } from "../api";
import type { EvalVersion } from "../types";

const percent = (value: number | null) => (value === null ? "—" : `${(value * 100).toFixed(1)}%`);

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

  /* The best category accuracy across every run, so the strongest number is
     marked rather than left for the reader to find. */
  const best = Math.max(
    ...versions.flatMap((version) => version.runs.map((run) => run.category_accuracy)),
  );

  const withTools = versions
    .flatMap((version) => version.runs)
    .filter((run) => run.tools_enabled);
  const noTools = versions.flatMap((version) => version.runs).filter((run) => !run.tools_enabled);
  const bestNoTools = noTools.length
    ? Math.max(...noTools.map((run) => run.category_accuracy))
    : null;
  const cheapest = Math.min(...withTools.map((run) => run.cost_per_ticket_usd));

  return (
    <div className="work work-center">
      <div className="stat-row">
        <div className="card stat good">
          <span className="big">{percent(best)}</span>
          <span className="cap">best category accuracy, with tools</span>
        </div>
        <div className="card stat bad">
          <span className="big">{bestNoTools === null ? "—" : percent(bestNoTools)}</span>
          <span className="cap">best the model manages with no tools</span>
        </div>
        <div className="card stat">
          <span className="big">${cheapest.toFixed(4)}</span>
          <span className="cap">cost per ticket at that accuracy</span>
        </div>
      </div>

      <div className="card">
        <span className="eyebrow">Accuracy by ticket-set version</span>
        <div className="table-scroll">
          <table className="eval">
            <caption>
              Each version is the same 48 patients and the same ground truth — only the wording of
              the complaints and the tools available changed.
            </caption>
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
                  <tr key={`${version.version}-${run.label}`}>
                    {index === 0 && (
                      <th scope="row" rowSpan={version.runs.length}>
                        {version.version}
                      </th>
                    )}
                    <td>{run.label}</td>
                    <td>
                      <span className={run.tools_enabled ? "pill on" : "pill"}>
                        {run.tools_enabled ? "tools" : "text only"}
                      </span>
                    </td>
                    <td>{run.tickets}</td>
                    <td className={run.category_accuracy === best ? "best" : undefined}>
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
