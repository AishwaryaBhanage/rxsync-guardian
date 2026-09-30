import { categoryMeta, confidenceBand } from "../lib/diagnosis";
import type { Diagnosis } from "../types";

/** The supporting detail beside the draft: what to do, which prescription,
 *  and which records were cited. */
export function EvidencePanel({
  diagnosis,
  unverified,
}: {
  diagnosis: Diagnosis;
  unverified: string[];
}) {
  const meta = categoryMeta(diagnosis.category);
  const band = confidenceBand(diagnosis.confidence);

  return (
    <section className="card" aria-labelledby="ev-heading">
      <h3 className="eyebrow" id="ev-heading">
        Evidence
      </h3>

      <div className={`callout callout-${band.name === "high" ? "info" : "warn"}`}>
        <span className="ico" aria-hidden="true">
          {band.name === "high" ? "→" : "!"}
        </span>
        <span>
          <strong>{band.guidance}</strong>
        </span>
      </div>

      <dl className="facts">
        <dt>Next step</dt>
        <dd>{meta.action}</dd>

        <dt>Prescription</dt>
        <dd>
          {diagnosis.rx_number ? (
            <span className="rx">{diagnosis.rx_number}</span>
          ) : (
            <span className="muted">none identified</span>
          )}
        </dd>

        <dt>Records cited</dt>
        <dd>
          {diagnosis.evidence.length === 0 ? (
            <span className="muted">none</span>
          ) : (
            <ul className="chips">
              {diagnosis.evidence.map((id) => {
                const bad = unverified.includes(id);
                return (
                  <li key={id} className={bad ? "chip is-unverified" : "chip"}>
                    {bad && (
                      <span className="flag" aria-hidden="true">
                        !
                      </span>
                    )}
                    {id}
                    {bad && <span className="sr-only"> — unverified</span>}
                  </li>
                );
              })}
            </ul>
          )}
        </dd>
      </dl>

      {unverified.length > 0 && (
        <div className="callout callout-warn">
          <span className="ico" aria-hidden="true">
            !
          </span>
          <span>
            {unverified.length === 1
              ? "1 cited record was not returned by any tool in this run"
              : `${unverified.length} cited records were not returned by any tool in this run`}
            , so it could not be checked. Do not quote it to the patient.
          </span>
        </div>
      )}
    </section>
  );
}
