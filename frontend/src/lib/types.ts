export type SessionStatus = "new" | "ingesting" | "analyzing" | "ready" | "archived";
export type InferredKind = "string" | "int" | "float" | "date" | "datetime" | "bool" | "category";
export type LinkDirection = "many_to_one" | "one_to_one" | "many_to_many";
export type WidgetKind = "kpi" | "bar" | "line" | "pie" | "table" | "markdown";
export type LinkSource = "discovered" | "user_added";
export type LinkAction = "confirm" | "reject" | "edit";
export type PageKind = "overview" | "file" | "question";
export type CellKind = "python" | "sql" | "widget";
export type RunStatus = "ok" | "error" | "stale";
export type TurnState = "pending" | "tool_running" | "rendering" | "complete" | "failed";
export type MessageRole = "user" | "assistant" | "tool" | "system";

export type Session = {
  id: string;
  name: string;
  status: SessionStatus;
  created_at: string;
};

export type FileRecord = {
  id: string;
  session_id: string;
  filename: string;
  parquet_path: string;
  row_count: number;
  schema_version: number;
  created_at: string;
};

export type SchemaColumn = {
  file_id: string;
  schema_version: number;
  name: string;
  dtype: string;
  inferred_kind: InferredKind;
  confidence: number;
  position: number;
};

export type FileSchema = {
  file_id: string;
  schema_version: number;
  columns: SchemaColumn[];
};

export type FileSchemaResponse = {
  file: FileRecord;
  schema: FileSchema;
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

export type NotebookCell = {
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
  cells_by_page: Record<string, NotebookCell[]>;
};

export type BuildDashboardResponse = {
  page_id: string;
  anomaly_count: number;
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
