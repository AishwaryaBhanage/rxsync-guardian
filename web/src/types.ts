export type Category =
  | "duplicate"
  | "dropped"
  | "stale_status"
  | "phantom_schedule"
  | "no_issue_found";

export interface Diagnosis {
  category: Category;
  rx_number: string | null;
  evidence: string[];
  confidence: number;
  draft_reply: string;
}

export interface ToolCall {
  name: string;
  input: Record<string, unknown>;
  output: unknown;
  is_error: boolean;
}

export interface Trace {
  model: string;
  api_calls: number;
  tool_calls: ToolCall[];
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_s: number;
  stop_reason: string | null;
}

export interface Investigation {
  diagnosis: Diagnosis | null;
  trace: Trace;
  unverified_evidence: string[];
  incomplete_reason: string | null;
  cost_usd: number;
  latency_s: number;
  model: string;
}

export interface Example {
  ticket_id: string;
  patient_id: string;
  text: string;
  expected_category: Category;
}

export interface EvalRun {
  label: string;
  model: string;
  tools_enabled: boolean;
  tickets: number;
  category_accuracy: number;
  rx_accuracy: number | null;
  rx_scored: number;
  cost_per_ticket_usd: number;
  avg_tool_calls: number;
}

export interface EvalVersion {
  version: string;
  note: string;
  generated_at: string | null;
  runs: EvalRun[];
}

export const MODELS = ["claude-haiku-4-5", "claude-sonnet-5"] as const;
export type Model = (typeof MODELS)[number];
