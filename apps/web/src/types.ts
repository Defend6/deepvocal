export type Agent = "knowledge" | "assessment" | "planning" | "singing";
export type Metric = {
  available: boolean;
  value: number | null;
  unit?: string;
  reason?: string;
};
export type Citation = {
  number: number;
  title: string;
  url: string;
  text: string;
  license: string;
  review_status: string;
};
export type Result = {
  status: string;
  reason?: string;
  message?: string;
  answer?: string | null;
  mode?: string;
  review_notice?: string;
  evidence_preview?: Citation[];
  citations?: Citation[];
  total?: Metric;
  dimensions?: Record<string, Metric>;
  quality?: string;
  duration_ms?: number;
  tracking_coverage?: number;
  algorithm_version?: string;
  ruleset_version?: string;
  limitations?: string[];
  report_id?: string;
  plan_id?: string;
  segments?: {
    start_ms: number;
    end_ms: number;
    coverage: number;
    mean_abs_cents: number | null;
  }[];
  pitch_contour?: { time: number; hz: number | null }[];
  reference_contour?: {start: number; end: number; hz: number}[];
  asset_id?: string;
  plan?: Plan["payload"];
  steps?: {id: string; agent: Agent; title: string; status: string; message?: string; result?: Result}[];
};
export type Job = {
  id: string;
  kind: Agent;
  status: string;
  result: Result | null;
  error_code: string | null;
  created_at: string;
  attempt: number;
  trace_id: string;
  updated_at?: string;
  show_evidence?: boolean;
};
export type Exercise = { title: string; seconds: number; instruction: string };
export type Plan = {
  id: string;
  version: number;
  payload: {
    goal: string;
    goals?: string[];
    days: number;
    minutes: number;
    exercises: Exercise[];
    notice: string;
    stop_rule: string;
    observations: string[];
  };
};
export type Report = { id: string; created_at: string; payload: Result };
export type Practice = {
  id: string;
  plan_version: number;
  payload: {
    duration_seconds: number;
    difficulty: number;
    discomfort: boolean;
    note: string;
  };
  created_at: string;
};
export type ConversationTurn = {
  id?: string;
  created_at?: string;
  user_text: string;
  response: {status?: string; message?: string; job?: Job; show_evidence?: boolean};
};
export type ConversationSummary = {id: string; title: string; revision: number};
