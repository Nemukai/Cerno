export type SessionStatus = "new" | "ingesting" | "analyzing" | "ready" | "archived";
export type DiscoveryStatus = "empty" | "discovering" | "pending_review" | "approved" | "failed";
export type SimpleDtype = "string" | "int" | "float" | "date" | "datetime" | "bool" | "category";
export type InferredKind = SimpleDtype;
export type LinkDirection = "many_to_one" | "one_to_one" | "many_to_many";
export type WidgetKind = "kpi" | "bar" | "line" | "pie" | "table" | "markdown";
export type LinkSource = "discovered" | "user_added";
export type LinkAction = "confirm" | "reject" | "edit";
export type PageKind = "overview" | "file" | "question";
export type CellKind = "python" | "sql" | "widget";
export type RunStatus = "ok" | "error" | "stale";
export type TurnState = "pending" | "tool_running" | "rendering" | "complete" | "failed";
export type MessageRole = "user" | "assistant" | "tool" | "system";
export type ProcessingEventKind =
  | "queued"
  | "uploading"
  | "ingesting_file"
  | "started"
  | "loading_artifacts"
  | "reading_files"
  | "python_analysis"
  | "calling_llm"
  | "parsing_response"
  | "saving_schema"
  | "resolving_links"
  | "applying_schema"
  | "reingesting_file"
  | "done"
  | "error";

export type Session = {
  id: string;
  name: string;
  status: SessionStatus;
  discovery_status: DiscoveryStatus;
  overview: string | null;
  created_at: string;
};

export type FileRecord = {
  id: string;
  session_id: string;
  filename: string;
  parquet_path: string;
  raw_parquet_path: string | null;
  original_size_bytes: number | null;
  row_count: number;
  schema_version: number;
  header_row: number | null;
  friendly_name: string | null;
  description: string | null;
  content_hash: string | null;
  created_at: string;
};

export type ProcessingEvent = {
  id: number;
  session_id: string;
  kind: ProcessingEventKind;
  message: string;
  created_at: string;
  job_id: string | null;
  step_key: string | null;
  level: string | null;
  progress: number | null;
  details: Record<string, unknown>;
};

export type ProcessingJobResponse = {
  job_id: string;
  session_id: string;
  kind: string;
  job_status: string;
  discovery_status: DiscoveryStatus;
  events: ProcessingEvent[];
};

export type DiscoveredColumn = {
  column_id: string;
  name: string;
  description: string;
  dtype: SimpleDtype;
};

export type DiscoveredFile = {
  file_id: string;
  friendly_name: string;
  description: string;
  header_row: number;
  columns: DiscoveredColumn[];
};

export type DiscoveredLink = {
  file_a_id: string;
  col_a: string;
  file_b_id: string;
  col_b: string;
  direction: LinkDirection;
  summary: string;
};

export type DiscoveryResponse = {
  session_id: string;
  status: DiscoveryStatus;
  files: DiscoveredFile[];
  links: DiscoveredLink[];
  overview: string;
};

export type FilePreviewResponse = {
  file_id: string;
  columns: string[];
  rows: unknown[][];
  total_rows: number;
};

export type SchemaColumn = {
  file_id: string;
  schema_version: number;
  name: string;
  dtype: string;
  inferred_kind: InferredKind;
  confidence: number;
  position: number;
  column_id: string | null;
  description: string | null;
};

export type FileSchema = {
  file_id: string;
  schema_version: number;
  columns: SchemaColumn[];
};

export type Link = {
  id: string;
  session_id: string;
  file_a: string;
  col_a: string;
  file_b: string;
  col_b: string;
  overlap: number;
  direction: LinkDirection;
  score: number;
  summary: string | null;
  source: LinkSource;
  created_at: string;
};

export type LinkReview = {
  id: number | null;
  link_id: string;
  action: LinkAction;
  notes: string | null;
  created_at: string;
};

export type Anomaly = {
  id: string;
  session_id: string;
  file_id: string;
  row_id: number;
  detector: string;
  reason_plain: string;
  reason_technical: string;
  score_normalized: number;
  score_raw: number;
  source_code: string;
  review_status: "dismissed" | "escalated" | null;
  reviewed_at: string | null;
  reviewed_by: string | null;
  notes: string | null;
  created_at: string;
};

export type DashboardPage = {
  id: string;
  dashboard_id: string;
  title: string;
  kind: PageKind;
  source_chat_turn_id: string | null;
  pinned: boolean;
  position: number;
  created_at: string;
};

export type DashboardCell = {
  id: string;
  page_id: string;
  order_index: number;
  kind: CellKind;
  code: string;
  output: Record<string, unknown> | null;
  bound_file_ids: string[];
  bound_schema_versions: Record<string, number>;
  threshold_snapshot: Record<string, unknown>;
  last_run_at: string | null;
  last_run_status: RunStatus | null;
  created_at: string;
};

export type ChatTurn = {
  id: string;
  session_id: string;
  user_message: string;
  assistant_message: string | null;
  spawned_page_id: string | null;
  state: TurnState;
  created_at: string;
};

export type ChatMessage = {
  id: string;
  turn_id: string;
  role: MessageRole;
  content: string;
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
  tool_result: Record<string, unknown> | null;
  created_at: string;
};

export type Widget = {
  kind: WidgetKind;
  title: string;
  data: Record<string, unknown>;
  options: Record<string, unknown>;
  caption: string | null;
};

export type DashboardResponse = {
  pages: DashboardPage[];
  cells_by_page: Record<string, DashboardCell[]>;
};

export type DataDocColumn = {
  name: string;
  dtype: string;
  meaning: string;
  role: string | null;
};

export type DataDocFile = {
  file_id: string;
  name: string;
  description: string;
  grain: string;
  row_count: number;
  columns: DataDocColumn[];
  key_columns: string[];
  date_columns: string[];
  measure_columns: string[];
  category_columns: string[];
  caveats: string[];
};

export type DataDocRelationship = {
  left_file_id: string;
  left_column: string;
  right_file_id: string;
  right_column: string;
  explanation: string;
};

export type DataDocGlossaryItem = {
  term: string;
  meaning: string;
};

export type DataDoc = {
  session_id: string;
  overview: string;
  files: DataDocFile[];
  relationships: DataDocRelationship[];
  glossary: DataDocGlossaryItem[];
  usage_notes: string[];
  starter_questions: string[];
  created_at: string;
  updated_at: string;
};

export type BuildDashboardResponse = {
  page_id: string;
  widget_count: number;
};

export type ChatResponse = {
  turn_id: string;
  assistant_message: string;
  spawned_page_id: string | null;
  widgets: Widget[];
};

export type KpiData = {
  value: string | number;
  label?: string;
  delta?: {
    direction: "up" | "down";
    value: string;
  };
};
