import type { Category, Model } from "../types";

export interface CategoryMeta {
  /** Plain-English heading a support agent can read aloud to a patient. */
  label: string;
  /** One line on what it means for this patient, shown under the heading. */
  meaning: string;
  /** What the agent should do next. Support staff act on this, not the slug. */
  action: string;
  /** CSS custom property holding the category hue. */
  hue: string;
  /** Text glyph, announced to screen readers via the label, not on its own. */
  glyph: string;
}

export const CATEGORY_META: Record<Category, CategoryMeta> = {
  duplicate: {
    label: "Duplicate record",
    meaning: "The same prescription is listed twice, spelled differently each time.",
    action: "Reassure the patient they were not double-charged or double-dispensed, then raise a record merge.",
    hue: "var(--cat-duplicate)",
    glyph: "⧉",
  },
  dropped: {
    label: "Missing from the app",
    meaning: "The pharmacy holds this prescription but it never reached the patient's app.",
    action: "Confirm the prescription is real and active, and ask the pharmacy to re-send the feed.",
    hue: "var(--cat-dropped)",
    glyph: "!",
  },
  stale_status: {
    label: "Out-of-date status",
    meaning: "The app is showing an older status than the pharmacy's own records.",
    action: "Tell the patient the real current status and flag the sync lag.",
    hue: "var(--cat-stale)",
    glyph: "◷",
  },
  phantom_schedule: {
    label: "Refill never happened",
    meaning: "The app implies an automatic refill that the pharmacy never actually filled.",
    action: "Arrange the refill manually — do not assume it is already in progress.",
    hue: "var(--cat-phantom)",
    glyph: "◌",
  },
  no_issue_found: {
    label: "No issue found",
    meaning: "The app and the pharmacy records agree. Nothing is out of sync.",
    action: "Explain what the patient is seeing and why it is correct.",
    hue: "var(--cat-none)",
    glyph: "✓",
  },
};

export function categoryMeta(category: string): CategoryMeta {
  return (
    CATEGORY_META[category as Category] ?? {
      label: category,
      meaning: "Unrecognised category returned by the agent.",
      action: "Review the trace below before replying.",
      hue: "var(--brand)",
      glyph: "?",
    }
  );
}

export type BandName = "high" | "medium" | "low";

export interface Band {
  name: BandName;
  /** Shown next to the percentage; the percentage alone is not guidance. */
  label: string;
  /** Explicit instruction, because "72%" does not tell anyone what to do. */
  guidance: string;
}

/** Bands match the eval's calibration buckets so the UI and the report agree. */
export function confidenceBand(confidence: number): Band {
  if (confidence >= 0.85) {
    return {
      name: "high",
      label: "High confidence",
      guidance: "Evidence is consistent. Read the draft, then send.",
    };
  }
  if (confidence >= 0.6) {
    return {
      name: "medium",
      label: "Medium confidence",
      guidance: "Open the cited records below before you send this.",
    };
  }
  return {
    name: "low",
    label: "Low confidence",
    guidance: "Treat this as a hint only. Verify manually before replying.",
  };
}

/** Human labels and per-ticket cost, so the picker is not raw model ids. */
export const MODEL_META: Record<Model, { label: string; note: string }> = {
  "claude-haiku-4-5": { label: "Haiku 4.5", note: "fast · ~$0.012 a ticket" },
  "claude-sonnet-5": { label: "Sonnet 5", note: "slower · ~3–4× the cost" },
};
