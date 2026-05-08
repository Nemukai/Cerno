export type SessionStatus = "new" | "ingesting" | "analyzing" | "ready" | "archived";
export type DiscoveryStatus = "empty" | "discovering" | "pending_review" | "approved" | "failed";
export type SimpleDtype = "string" | "int" | "float" | "date" | "datetime" | "bool" | "category";
export type InferredKind = SimpleDtype;
export type LinkDirection = "many_to_one" | "one_to_one" | "many_to_many";
export type WidgetKind =
  | "kpi"
  | "bar"
  | "horizontal_bar"
  | "grouped_bar"
  | "stacked_bar"
  | "line"
  | "area"
  | "stacked_area"
  | "pie"
  | "histogram"
  | "scatter"
  | "heatmap"
  | "boxplot"
  | "waterfall"
  | "sankey"
  | "timeline"
  | "table"
  | "markdown";
export type LinkSource = "discovered" | "user_added";
export type LinkAction = "confirm" | "reject" | "edit";
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

export type ChatTurn = {
  id: string;
  session_id: string;
  user_message: string;
  assistant_message: string | null;
  spawned_page_id: string | null;
  title: string | null;
  metadata: Record<string, unknown>;
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

export type ChatArtifact = {
  id: string;
  session_id: string;
  turn_id: string | null;
  message_id: string | null;
  artifact_type: string;
  title: string;
  inline_payload: Record<string, unknown> | null;
  storage_backend: string | null;
  object_key: string | null;
  size_bytes: number;
  mime_type: string | null;
  order_index: number;
  created_at: string;
};

export type ChatFeedTurn = {
  turn: ChatTurn;
  messages: ChatMessage[];
  artifacts: ChatArtifact[];
};

export type WorkspaceResponse = {
  session: Session;
  files: FileRecord[];
  links: Link[];
  discovery: DiscoveryResponse;
  events: ProcessingEvent[];
  chat_feed: ChatFeedTurn[];
  data_doc: DataDoc | null;
};

export type ChatStreamEvent =
  | {
      type: "turn_started";
      turn_id: string;
      conversation_id: string;
      message: string;
    }
  | {
      type: "reasoning_delta" | "assistant_delta" | "tool_call_arguments_delta";
      turn_id: string;
      delta: string;
      call_id?: string;
    }
  | {
      type: "tool_call_started";
      turn_id: string;
      call_id: string;
      name: string;
    }
  | {
      type: "tool_call_done";
      turn_id: string;
      call_id: string;
      name: string;
      arguments: Record<string, unknown>;
    }
  | {
      type: "tool_result";
      turn_id: string;
      call_id: string;
      name: string;
      result: Record<string, unknown>;
    }
  | {
      type: "done";
      turn_id: string;
      conversation_id: string;
      assistant_message: string;
      response_id: string | null;
      widgets: Widget[];
    }
  | {
      type: "error";
      turn_id: string;
      message: string;
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

export type ChatResponse = {
  turn_id: string;
  assistant_message: string;
  spawned_page_id: string | null;
  widgets: Widget[];
};

export type KpiData = {
  value: string | number;
  label?: string;
  title?: string;
  items?: Array<{
    title?: string;
    value: string | number;
    label?: string;
    delta?: {
      direction: "up" | "down";
      value: string;
    };
  }>;
  delta?: {
    direction: "up" | "down";
    value: string;
  };
};
