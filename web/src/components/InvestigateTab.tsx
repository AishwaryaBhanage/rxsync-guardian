import { useEffect, useMemo, useState } from "react";
import { getExamples, getPatients, investigate } from "../api";
import { MODEL_META, categoryMeta } from "../lib/diagnosis";
import { MODELS, type Example, type Investigation, type Model } from "../types";
import { DraftReply } from "./DraftReply";
import { EvidencePanel } from "./EvidencePanel";
import { PatientPicker } from "./PatientPicker";
import { TraceList } from "./TraceList";
import { VerdictBanner } from "./VerdictBanner";

export function InvestigateTab() {
  const [patients, setPatients] = useState<string[]>([]);
  const [examples, setExamples] = useState<Example[]>([]);
  const [patientId, setPatientId] = useState("");
  const [ticket, setTicket] = useState("");
  const [model, setModel] = useState<Model>(MODELS[0]);
  const [filter, setFilter] = useState("");
  const [result, setResult] = useState<Investigation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getPatients(), getExamples()])
      .then(([loadedPatients, loadedExamples]) => {
        setPatients(loadedPatients);
        setExamples(loadedExamples);
        setPatientId(loadedPatients[0] ?? "");
      })
      .catch((cause: Error) => setError(cause.message));
  }, []);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return examples;
    return examples.filter(
      (example) =>
        example.text.toLowerCase().includes(needle) ||
        example.ticket_id.toLowerCase().includes(needle) ||
        example.patient_id.toLowerCase().includes(needle),
    );
  }, [examples, filter]);

  function useExample(example: Example) {
    setPatientId(example.patient_id);
    setTicket(example.text);
    setResult(null);
    setError(null);
  }

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await investigate(patientId, ticket, model));
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const ready = patientId.length > 0 && ticket.trim().length > 0 && !busy;

  /* Cmd/Ctrl+Enter submits from inside the textarea — the shortcut anyone who
     lives in a support queue will reach for. */
  function onTicketKeyDown(event: React.KeyboardEvent) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && ready) {
      event.preventDefault();
      void run();
    }
  }

  return (
    <div className="workspace">
      <aside className="rail" aria-label="Example tickets">
        <div className="rail-head">
          <div className="rail-title">
            <span className="eyebrow">Ticket queue</span>
            <span className="rail-count">
              {shown.length}/{examples.length}
            </span>
          </div>
          <label className="sr-only" htmlFor="example-filter">
            Filter example tickets
          </label>
          <input
            id="example-filter"
            type="search"
            placeholder="Filter tickets…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </div>

        <ul className="queue">
          {shown.length === 0 && <li className="queue-empty">Nothing matches that filter.</li>}
          {shown.map((example) => (
            <li key={example.ticket_id}>
              <button
                type="button"
                className="queue-item"
                aria-current={ticket === example.text}
                onClick={() => useExample(example)}
                title={example.text}
              >
                <span className="queue-top">
                  <span className="queue-id">{example.ticket_id}</span>
                  <span className="queue-pid">{example.patient_id}</span>
                </span>
                <span className="queue-text">{example.text}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="work">
        <form
          className="card composer"
          onSubmit={(event) => {
            event.preventDefault();
            if (ready) void run();
          }}
        >
          <div className="card-title">
            <span className="eyebrow">The ticket</span>
            <span className="hint">{MODEL_META[model].note}</span>
          </div>

          <div className="composer-grid">
            <PatientPicker patients={patients} value={patientId} onChange={setPatientId} />

            <div className="field">
              <label className="label" htmlFor="complaint">
                What the patient told us
              </label>
              <textarea
                id="complaint"
                rows={4}
                value={ticket}
                placeholder="Something looks off with my metformin…"
                onChange={(event) => setTicket(event.target.value)}
                onKeyDown={onTicketKeyDown}
              />
            </div>
          </div>

          <div className="composer-actions">
            <div className="field" style={{ maxWidth: 260, flex: "1 1 200px" }}>
              <label className="label" htmlFor="model">
                Model
              </label>
              <select
                id="model"
                value={model}
                onChange={(event) => setModel(event.target.value as Model)}
              >
                {MODELS.map((id) => (
                  <option key={id} value={id}>
                    {MODEL_META[id].label} — {MODEL_META[id].note}
                  </option>
                ))}
              </select>
            </div>

            <button type="submit" className="primary" disabled={!ready}>
              {busy ? "Investigating…" : "Investigate"}
              {!busy && <kbd aria-hidden="true">⌘↵</kbd>}
            </button>
          </div>
        </form>

        {/* Results are announced, because otherwise a screen-reader user gets
            no signal that an 8-second request finished. */}
        <div aria-live="polite" aria-busy={busy}>
          {error && (
            <div className="callout callout-error" role="alert">
              <span className="ico" aria-hidden="true">
                !
              </span>
              <span>
                <strong>That did not run.</strong> {error}
              </span>
            </div>
          )}

          {busy && (
            <div className="card working">
              <div className="row">
                <span className="spinner" aria-hidden="true" />
                <span>Reading the pharmacy records…</span>
              </div>
              <div className="skeleton sk-title" />
              <div className="skeleton sk-line" />
              <div className="skeleton sk-block" />
              <span className="sr-only">Investigating. This usually takes about 8 seconds.</span>
            </div>
          )}

          {result && !busy && (
            <span className="sr-only">
              {result.diagnosis
                ? `Finished. Diagnosis: ${categoryMeta(result.diagnosis.category).label}, ${Math.round(
                    result.diagnosis.confidence * 100,
                  )} percent confidence.`
                : "Finished without a diagnosis."}
            </span>
          )}
        </div>

        {result && !busy && (
          <>
            {result.diagnosis ? (
              <>
                <VerdictBanner diagnosis={result.diagnosis} />
                <div className="result-grid">
                  <DraftReply text={result.diagnosis.draft_reply} />
                  <EvidencePanel
                    diagnosis={result.diagnosis}
                    unverified={result.unverified_evidence}
                  />
                </div>
              </>
            ) : (
              <section className="card">
                <span className="eyebrow">Diagnosis</span>
                <div className="callout callout-warn">
                  <span className="ico" aria-hidden="true">
                    !
                  </span>
                  <span>
                    <strong>No diagnosis.</strong> {result.incomplete_reason}
                  </span>
                </div>
              </section>
            )}
            <TraceList trace={result.trace} />
          </>
        )}

        {!result && !busy && !error && (
          <div className="empty">
            <span className="big" aria-hidden="true">
              ⌕
            </span>
            <p>
              Pick a ticket from the queue, or type a complaint above, then press Investigate.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
