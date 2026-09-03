export type Priority = "high" | "medium" | "low";
export type Category = "regulation" | "reputation" | "competitors" | "trends";

export interface Evidence { claim: string; quote: string }
export interface Analysis {
  item_id: string;
  entity_type: "news" | "regulation";
  short_title: string;
  summary: string;
  who: string[];
  what: string;
  when: string | null;
  consequences: string[];
  affected_products: string[];
  primary_category: Category;
  tags: string[];
  impact_type: "risk" | "opportunity" | "mixed" | "none" | "unknown";
  impact_for_gs_labs: string;
  suggested_priority: Priority;
  effective_priority: Priority;
  priority_reason: string;
  review_required: boolean;
  evidence: Evidence[];
  uncertainties: string[];
  regulation: {
    identifier: string | null;
    document_type: string | null;
    stage: string | null;
    effective_date: string | null;
    next_checkpoint: string | null;
  } | null;
}

export interface AuditEntry {
  id: number;
  field: string;
  old_value: unknown;
  new_value: unknown;
  reason: string | null;
  created_at: string;
  user: string;
}

export interface Item {
  id: string;
  cluster_id: string;
  title: string;
  text: string;
  url: string | null;
  author: string | null;
  published_at: string | null;
  fetched_at: string;
  source: { id: string; name: string; type: string };
  hidden: boolean;
  analysis: Analysis | null;
  manual_priority: Priority | null;
  display_priority: Priority | null;
  cluster_sources: Array<{ item_id: string; source: string; source_type: string; url: string | null; published_at: string | null }>;
  audit: AuditEntry[];
  analysis_meta: { version: number; model: string; prompt_version: string; latency_ms: number; created_at: string } | null;
}

export interface Source {
  id: string;
  name: string;
  type: string;
  url: string | null;
  enabled: boolean;
  last_success: string | null;
  last_error: string | null;
}

export interface RegulationCase {
  id: string;
  identifier: string | null;
  title: string;
  stage: string | null;
  effective_date: string | null;
  next_checkpoint: string | null;
  timeline: Array<{ item_id: string; date: string; stage: string; title: string; url: string | null }>;
  item_ids: string[];
}

export interface Stats {
  raw_items: number;
  events: number;
  analyzed: number;
  high: number;
  review: number;
  regulations: number;
  average_latency_ms: number;
  demo_mode: boolean;
  provider: string;
}
