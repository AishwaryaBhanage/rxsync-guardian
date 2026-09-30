import { useEffect, useRef, useState } from "react";

export function DraftReply({ text }: { text: string }) {
  const [draft, setDraft] = useState(text);
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const area = useRef<HTMLTextAreaElement | null>(null);

  // A fresh investigation replaces the draft rather than keeping the last edit.
  useEffect(() => {
    setDraft(text);
    setCopied(false);
    setFailed(false);
  }, [text]);

  async function approveAndCopy() {
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      setFailed(false);
      window.setTimeout(() => setCopied(false), 3000);
    } catch {
      // Clipboard access can be blocked; select the text so Cmd+C still works.
      setFailed(true);
      area.current?.select();
    }
  }

  const edited = draft !== text;

  return (
    <section className="card draft-card" aria-labelledby="draft-heading">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 className="eyebrow" id="draft-heading">
          Draft reply
        </h3>
        {edited && <span className="hint">edited</span>}
      </div>

      <label className="field">
        <span className="sr-only">Draft reply to the patient</span>
        <textarea
          ref={area}
          className="draft"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={7}
        />
      </label>
      <span className="count">{draft.length} characters</span>

      <div className="draft-foot">
        <div className="row">
          <button type="button" className="primary" onClick={approveAndCopy}>
            Approve &amp; copy
          </button>
          {/* Announced to screen readers the moment it changes. */}
          <span role="status" aria-live="polite">
            {copied && <span className="copied">Copied to clipboard</span>}
            {failed && (
              <span className="hint">Clipboard blocked — the text is selected, press Cmd+C</span>
            )}
          </span>
        </div>
        <p className="note">A human reviews every reply. Nothing is sent automatically.</p>
      </div>
    </section>
  );
}
