import { categoryMeta, confidenceBand } from "../lib/diagnosis";
import type { Diagnosis } from "../types";

/** The headline: what it found and how much to trust it. Read first, so it
 *  sits full-width above everything else. */
export function VerdictBanner({ diagnosis }: { diagnosis: Diagnosis }) {
  const meta = categoryMeta(diagnosis.category);
  const band = confidenceBand(diagnosis.confidence);
  const percent = Math.round(diagnosis.confidence * 100);

  return (
    <section
      className="verdict"
      aria-labelledby="dx-heading"
      style={{ ["--cat" as string]: meta.hue }}
    >
      <span className="verdict-glyph" aria-hidden="true">
        {meta.glyph}
      </span>

      <div className="verdict-main">
        <h2 id="dx-heading">{meta.label}</h2>
        <code className="slug">{diagnosis.category}</code>
        <p className="meaning">{meta.meaning}</p>
      </div>

      <div className={`verdict-score band-${band.name}`}>
        <span className="pct">{percent}%</span>
        <span className="band">{band.label}</span>
        <div className="meter" aria-hidden="true">
          <span style={{ width: `${percent}%` }} />
        </div>
      </div>
    </section>
  );
}
