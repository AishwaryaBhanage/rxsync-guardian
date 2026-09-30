import { useId, useState } from "react";
import type { Trace } from "../types";

/** What each tool is for, in the support team's words rather than the API's. */
const TOOL_PURPOSE: Record<string, string> = {
  get_patient_view: "what the patient's app showed",
  get_pharmacy_records: "what the pharmacy's own records say",
  get_rx_history: "the fill and collection timeline",
  get_pharmacy_speed: "how fast that pharmacy usually works",
  submit_diagnosis: "the conclusion it settled on",
};

function summarise(output: unknown): string {
  if (Array.isArray(output)) return `${output.length} row${output.length === 1 ? "" : "s"}`;
  if (output && typeof output === "object") {
    const record = output as Record<string, unknown>;
    if ("error" in record) return String(record.error);
    if (Array.isArray(record.timeline)) return `${record.timeline.length} events`;
    return Object.keys(record).slice(0, 3).join(", ");
  }
  return String(output);
}

function Step({
  index,
  name,
  input,
  output,
  isError,
}: {
  index: number;
  name: string;
  input: Record<string, unknown>;
  output: unknown;
  isError: boolean;
}) {
  const [open, setOpen] = useState(false);
  const bodyId = `${useId()}-body`;
  const args = Object.values(input).map((value) => String(value)).join(", ");
  const purpose = TOOL_PURPOSE[name] ?? "tool call";

  return (
    <li className={isError ? "step is-error" : "step"}>
      <button
        type="button"
        className="step-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen(!open)}
      >
        <span className="step-n" aria-hidden="true">
          {index}
        </span>
        <span className="step-name">
          {name}
          {args && <span className="step-args">({args})</span>}
          <span className="sr-only"> — {purpose}</span>
        </span>
        <span className="step-sum">{isError ? "error" : summarise(output)}</span>
        <span className="chevron" aria-hidden="true">
          {open ? "▲" : "▼"}
        </span>
      </button>
      <pre className="step-body" id={bodyId} hidden={!open}>
        {JSON.stringify(output, null, 2)}
      </pre>
    </li>
  );
}

export function TraceList({ trace }: { trace: Trace }) {
  return (
    <details className="card trace-card trace">
      <summary>
        <span className="caret" aria-hidden="true">
          ▶
        </span>
        How it worked this out
        <span className="hint" style={{ marginLeft: "auto", fontWeight: 400 }}>
          {trace.tool_calls.length} steps · {trace.latency_s.toFixed(1)}s · $
          {trace.cost_usd.toFixed(4)}
        </span>
      </summary>

      <div className="trace-inner">
        <p className="hint">
          Every step below is a read-only lookup. Expand one to see exactly what came back.
        </p>

        <ol className="steps">
          {trace.tool_calls.map((call, index) => (
            <Step
              key={index}
              index={index + 1}
              name={call.name}
              input={call.input}
              output={call.output}
              isError={call.is_error}
            />
          ))}
        </ol>

        <p className="meta">
          <span>
            <b>{trace.tool_calls.length}</b> tool calls
          </span>
          <span>
            <b>{trace.api_calls}</b> model calls
          </span>
          <span>
            <b>{(trace.input_tokens + trace.output_tokens).toLocaleString()}</b> tokens
          </span>
          <span>
            <b>${trace.cost_usd.toFixed(4)}</b>
          </span>
          <span>
            <b>{trace.latency_s.toFixed(1)}s</b>
          </span>
          <span>{trace.model}</span>
        </p>
      </div>
    </details>
  );
}
