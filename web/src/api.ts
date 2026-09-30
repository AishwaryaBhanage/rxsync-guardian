import type { EvalVersion, Example, Investigation, Model } from "./types";

const BASE = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const KEY = import.meta.env.VITE_DEMO_KEY ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", "x-demo-key": KEY, ...init?.headers },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    // The API always answers with {"error": ...}, so surface that rather than a status code.
    throw new Error(body?.error ?? `${response.status} ${response.statusText}`);
  }
  return body as T;
}

export const getPatients = () =>
  request<{ patients: string[] }>("/patients").then((b) => b.patients);

export const getExamples = () =>
  request<{ examples: Example[] }>("/examples").then((b) => b.examples);

export const getReport = () =>
  request<{ versions: EvalVersion[] }>("/report").then((b) => b.versions);

export const investigate = (patient_id: string, ticket_text: string, model: Model) =>
  request<Investigation>("/investigate", {
    method: "POST",
    body: JSON.stringify({ patient_id, ticket_text, model }),
  });

export const getHealth = () =>
  request<{ ok: boolean; models: string[] }>("/health");

export const hasKey = () => KEY.length > 0;
