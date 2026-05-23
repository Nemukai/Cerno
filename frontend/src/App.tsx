import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  motion,
  useMotionValue,
  useSpring,
  useTransform,
  type MotionValue,
} from "framer-motion";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Building2,
  Clock3,
  Database,
  FileSpreadsheet,
  HardDrive,
  MessageSquare,
  RefreshCw,
  ShieldCheck,
  Table2,
  Users,
  type LucideIcon,
} from "lucide-react";
import {
  approveSchema,
  addOrganizationMember,
  createSession,
  deleteFile,
  deleteSession,
  deleteTurn,
  getChatFeed,
  getDiscovery,
  getOrganizationAdminDashboard,
  getOwnerDashboard,
  getProcessingEvents,
  getSchemaGuide,
  getWorkspace,
  listFiles,
  listLinks,
  listSessions,
  processSession,
  removeOrganizationMember,
  streamChat,
  updateOrganizationMemberRole,
  updateTurn,
  uploadFiles,
} from "./lib/api";
import type {
  ChatArtifact,
  ChatMessage,
  ChatStreamEvent,
  ChatTurn,
  DataDoc,
  DiscoveredFile,
  DiscoveredLink,
  DiscoveryResponse,
  FileRecord,
  Link,
  OwnerDashboard,
  OrganizationAdminDashboard,
  OrganizationMemberBody,
  ProcessingEvent,
  Session,
  WorkspaceResponse,
} from "./lib/types";
import { ChatSidebar } from "./components/ChatSidebar";
import { CernoLockup } from "./components/Brand";
import {
  FilesPanel,
  WorkspaceSidebar,
} from "./components/SessionPanels";
import { SchemaTab } from "./components/SchemaTab";
import { SessionHeader } from "./components/SessionHeader";
import { Shell, type TabKey } from "./components/Shell";
import { AuthGate } from "./components/AuthGate";
import { BetaGate } from "./components/BetaGate";
import { useUser, googleLoginUrl, type CurrentUser } from "./lib/auth";
import { trackEvent } from "./lib/analytics";

type WorkspaceMetric = {
  fileCount: number;
  rowCount: number;
  lastOpenedAt: string | null;
  loaded: boolean;
};

type LiveChatState = {
  turnId: string | null;
  userMessage: string;
  assistantText: string;
  reasoningText: string;
  tools: Array<{
    callId: string;
    name: string;
    argsText: string;
    args?: Record<string, unknown>;
    result?: Record<string, unknown>;
  }>;
  error: string | null;
};

type HomeView = "landing" | "sessions";

type AppRoute =
  | { kind: "landing" }
  | { kind: "owner" }
  | { kind: "org-admin"; organizationId: string }
  | { kind: "sessions" }
  | { kind: "session"; sessionId: string; tab: TabKey; legacyFiles?: boolean };

const SESSION_TABS: TabKey[] = ["ask", "insights"];
const USER_SAFE_CHAT_ERROR = "Something went wrong. Please try again.";

function isTabKey(value: string | undefined): value is TabKey {
  return SESSION_TABS.includes(value as TabKey);
}

function readRoute(): AppRoute {
  const parts = window.location.pathname.split("/").filter(Boolean);
  if (parts.length === 0) return { kind: "landing" };
  if (parts[0] === "owner") return { kind: "owner" };
  if (parts[0] === "organizations" && parts[2] === "admin" && parts[1]) {
    return { kind: "org-admin", organizationId: decodeURIComponent(parts[1]) };
  }
  if (parts[0] === "sessions") {
    if (parts.length === 1) return { kind: "sessions" };
    const sessionId = parts[1];
    if (!sessionId) return { kind: "sessions" };
    if (parts[2] === "files") {
      window.history.replaceState(
        {},
        "",
        `/sessions/${sessionId}/insights`,
      );
    }
    return {
      kind: "session",
      sessionId: decodeURIComponent(sessionId),
      tab: parts[2] === "files" ? "insights" : isTabKey(parts[2]) ? parts[2] : "ask",
      legacyFiles: parts[2] === "files",
    };
  }
  return { kind: "landing" };
}

function routePath(route: AppRoute): string {
  if (route.kind === "landing") return "/";
  if (route.kind === "owner") return "/owner";
  if (route.kind === "org-admin") {
    return `/organizations/${encodeURIComponent(route.organizationId)}/admin`;
  }
  if (route.kind === "sessions") return "/sessions";
  return `/sessions/${encodeURIComponent(route.sessionId)}/${route.tab}`;
}

function hasActiveProcessingEvents(events: ProcessingEvent[]): boolean {
  const stateByJob = new Map<string, ProcessingEvent["kind"]>();
  let latestUnscoped: ProcessingEvent["kind"] | null = null;
  for (const event of events) {
    if (event.job_id) {
      stateByJob.set(event.job_id, event.kind);
    } else {
      latestUnscoped = event.kind;
    }
  }
  if ([...stateByJob.values()].some((kind) => kind !== "done" && kind !== "error")) {
    return true;
  }
  return latestUnscoped !== null && latestUnscoped !== "done" && latestUnscoped !== "error";
}

export function App() {
  const initialRoute = useRef<AppRoute>(readRoute());
  const [route, setRoute] = useState<AppRoute>(initialRoute.current);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [homeView, setHomeView] = useState<HomeView>(
    initialRoute.current.kind === "landing" ? "landing" : "sessions",
  );
  const [session, setSession] = useState<Session | null>(null);
  const [workspaceMetrics, setWorkspaceMetrics] = useState<
    Record<string, WorkspaceMetric>
  >({});
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [discovery, setDiscovery] = useState<DiscoveryResponse | null>(null);
  const [events, setEvents] = useState<ProcessingEvent[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [messagesByTurn, setMessagesByTurn] = useState<
    Record<string, ChatMessage[]>
  >({});
  const [artifactsByTurn, setArtifactsByTurn] = useState<
    Record<string, ChatArtifact[]>
  >({});
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const [dataDoc, setDataDoc] = useState<DataDoc | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("ask");
  const [starting, setStarting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [approving, setApproving] = useState(false);
  const [sending, setSending] = useState(false);
  const [liveChat, setLiveChat] = useState<LiveChatState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const queryClient = useQueryClient();
  const [_navigationPending, startNavigationTransition] = useTransition();
  const bootstrapped = useRef(false);
  const pollRef = useRef<number | null>(null);

  const refreshSessions = useCallback(async () => {
    const list = await listSessions();
    setSessions(list);
    return list;
  }, []);

  const refreshFiles = useCallback(async (sessionId: string) => {
    const fs = await listFiles(sessionId);
    setFiles(fs);
  }, []);

  const refreshLinks = useCallback(async (sessionId: string) => {
    const ls = await listLinks(sessionId);
    setLinks(ls);
  }, []);

  const refreshDiscovery = useCallback(async (sessionId: string) => {
    const d = await getDiscovery(sessionId);
    setDiscovery(d);
  }, []);

  const refreshEvents = useCallback(async (sessionId: string) => {
    const e = await getProcessingEvents(sessionId);
    setEvents(e);
  }, []);

  const applyChatFeed = useCallback((feed: WorkspaceResponse["chat_feed"]) => {
    setTurns(feed.map((item) => item.turn));
    setMessagesByTurn(
      Object.fromEntries(feed.map((item) => [item.turn.id, item.messages])),
    );
    setArtifactsByTurn(
      Object.fromEntries(feed.map((item) => [item.turn.id, item.artifacts])),
    );
  }, []);

  const refreshChatFeed = useCallback(async (sessionId: string) => {
    const feed = await getChatFeed(sessionId);
    applyChatFeed(feed);
  }, [applyChatFeed]);

  const refreshSchemaGuide = useCallback(async (sessionId: string) => {
    try {
      const doc = await getSchemaGuide(sessionId);
      setDataDoc(doc);
    } catch {
      setDataDoc(null);
    }
  }, []);

  const applyWorkspace = useCallback(
    (workspace: WorkspaceResponse) => {
      setSession(workspace.session);
      setFiles(workspace.files);
      setLinks(workspace.links);
      setDiscovery(workspace.discovery);
      setEvents(workspace.events);
      setDataDoc(workspace.data_doc);
      applyChatFeed(workspace.chat_feed);
    },
    [applyChatFeed],
  );

  const prefetchWorkspace = useCallback(
    (sessionId: string) => {
      queryClient.prefetchQuery({
        queryKey: ["workspace", sessionId],
        queryFn: () => getWorkspace(sessionId),
        staleTime: 30_000,
      });
    },
    [queryClient],
  );

  const workspaceQuery = useQuery({
    queryKey: ["workspace", session?.id],
    queryFn: () => getWorkspace(session!.id),
    enabled: Boolean(session?.id),
    placeholderData: (previousData) => previousData,
  });

  const navigate = useCallback((next: AppRoute, options?: { replace?: boolean }) => {
    const nextPath = routePath(next);
    const currentPath = `${window.location.pathname}${window.location.search}`;
    if (currentPath !== nextPath) {
      if (options?.replace) {
        window.history.replaceState({}, "", nextPath);
      } else {
        window.history.pushState({}, "", nextPath);
      }
    }
    setRoute(readRoute());
  }, []);

  const navigateHome = useCallback(
    (view: HomeView) => {
      navigate(view === "landing" ? { kind: "landing" } : { kind: "sessions" });
    },
    [navigate],
  );

  const navigateSessionTab = useCallback(
    (sessionId: string, tab: TabKey, options?: { replace?: boolean }) => {
      navigate({ kind: "session", sessionId, tab }, options);
    },
    [navigate],
  );

  useEffect(() => {
    const onPopState = () => setRoute(readRoute());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (session) return;
    let cancelled = false;
    if (sessions.length === 0) {
      setWorkspaceMetrics({});
      return;
    }
    Promise.all(
      sessions.map(async (item) => {
        try {
          prefetchWorkspace(item.id);
          const workspaceFiles = await listFiles(item.id);
          return [
            item.id,
            {
              fileCount: workspaceFiles.length,
              rowCount: workspaceFiles.reduce(
                (sum, file) => sum + file.row_count,
                0,
              ),
              lastOpenedAt: getWorkspaceLastOpened(item.id),
              loaded: true,
            },
          ] as const;
        } catch {
          return [
            item.id,
            {
              fileCount: 0,
              rowCount: 0,
              lastOpenedAt: getWorkspaceLastOpened(item.id),
              loaded: false,
            },
          ] as const;
        }
      }),
    ).then((entries) => {
      if (!cancelled) setWorkspaceMetrics(Object.fromEntries(entries));
    });
    return () => {
      cancelled = true;
    };
  }, [session, sessions, prefetchWorkspace]);

  useEffect(() => {
    if (!workspaceQuery.data || !session) return;
    if (workspaceQuery.data.session.id !== session.id) return;
    startNavigationTransition(() => {
      applyWorkspace(workspaceQuery.data);
    });
  }, [applyWorkspace, session, startNavigationTransition, workspaceQuery.data]);

  useEffect(() => {
    if (!workspaceQuery.error || !session) return;
    setError((workspaceQuery.error as Error).message);
  }, [session, workspaceQuery.error]);

  useEffect(() => {
    const hasActiveEvent = hasActiveProcessingEvents(events);
    const shouldPollProcessing =
      processing || uploading || discovery?.status === "discovering" || hasActiveEvent;
    if (!session || !shouldPollProcessing) {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    const id = session.id;
    pollRef.current = window.setInterval(() => {
      Promise.all([getProcessingEvents(id), getDiscovery(id)])
        .then(([nextEvents, nextDiscovery]) => {
          setEvents(nextEvents);
          setDiscovery(nextDiscovery);
          const nextHasActiveEvent = hasActiveProcessingEvents(nextEvents);
          if (nextDiscovery.status === "discovering" || nextHasActiveEvent) return;
          setProcessing(false);
          queryClient.invalidateQueries({ queryKey: ["workspace", id] });
          return Promise.all([
            refreshFiles(id),
            refreshLinks(id),
            refreshSchemaGuide(id),
            refreshSessions(),
          ]);
        })
        .catch(() => undefined);
    }, 1500);
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [
    session,
    processing,
    uploading,
    discovery?.status,
    events,
    refreshFiles,
    refreshLinks,
    refreshSchemaGuide,
    refreshSessions,
  ]);

  const clearSessionState = useCallback(() => {
    setFiles([]);
    setLinks([]);
    setDiscovery(null);
    setEvents([]);
    setTurns([]);
    setMessagesByTurn({});
    setArtifactsByTurn({});
    setActiveTurnId(null);
    setDataDoc(null);
    setActiveTab("ask");
  }, []);

  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    refreshSessions()
      .catch((err: Error) => setError(err.message))
      .finally(() => setSessionsLoaded(true));
  }, [refreshSessions]);

  useEffect(() => {
    if (!sessionsLoaded) return;

    if (route.kind === "landing") {
      if (session) {
        setSession(null);
        clearSessionState();
      }
      setHomeView("landing");
      return;
    }

    if (route.kind === "sessions") {
      if (session) {
        setSession(null);
        clearSessionState();
      }
      setHomeView("sessions");
      return;
    }

    if (route.kind === "owner" || route.kind === "org-admin") {
      if (session) {
        setSession(null);
        clearSessionState();
      }
      setHomeView("sessions");
      return;
    }

    const nextSession =
      sessions.find((item) => item.id === route.sessionId) ??
      (session?.id === route.sessionId ? session : null);
    if (!nextSession) {
      if (session) {
        setSession(null);
        clearSessionState();
      }
      setHomeView("sessions");
      setError("Workspace not found or not available for this user.");
      navigate({ kind: "sessions" }, { replace: true });
      return;
    }

    setHomeView("sessions");
    if (route.legacyFiles) {
      navigateSessionTab(route.sessionId, "insights", { replace: true });
    }
    if (session?.id !== nextSession.id) {
      markWorkspaceOpened(nextSession.id);
      clearSessionState();
      setSession(nextSession);
    }
    if (activeTab !== route.tab) {
      startNavigationTransition(() => {
        setActiveTab(route.tab);
      });
    }
  }, [
    activeTab,
    clearSessionState,
    navigate,
    navigateSessionTab,
    route,
    session,
    sessions,
    sessionsLoaded,
    startNavigationTransition,
  ]);

  const handleCreateSession = useCallback(
    async (name: string) => {
      setError(null);
      setStarting(true);
      try {
        const s = await createSession(name);
        trackEvent("cerno_workspace_created", {
          session_id: s.id,
          organization_id: s.organization_id ?? null,
        });
        markWorkspaceOpened(s.id);
        clearSessionState();
        setHomeView("sessions");
        setSession(s);
        setActiveTab("insights");
        navigateSessionTab(s.id, "insights");
        await refreshSessions();
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setStarting(false);
      }
    },
    [clearSessionState, navigateSessionTab, refreshSessions],
  );

  const handleResumeSession = useCallback(
    (s: Session) => {
      trackEvent("cerno_workspace_opened", {
        session_id: s.id,
        organization_id: s.organization_id ?? null,
      });
      markWorkspaceOpened(s.id);
      prefetchWorkspace(s.id);
      clearSessionState();
      setHomeView("sessions");
      setSession(s);
      setActiveTab("ask");
      navigateSessionTab(s.id, "ask");
    },
    [clearSessionState, navigateSessionTab, prefetchWorkspace],
  );

  const handleHome = useCallback(() => {
    setSession(null);
    setHomeView("sessions");
    clearSessionState();
    navigateHome("sessions");
    refreshSessions().catch((err: Error) => setError(err.message));
  }, [clearSessionState, navigateHome, refreshSessions]);

  const handleDeleteSession = useCallback(
    async (id: string) => {
      setError(null);
      try {
        await deleteSession(id);
        trackEvent("cerno_workspace_deleted", { session_id: id });
        setWorkspaceMetrics((current) => {
          const next = { ...current };
          delete next[id];
          return next;
        });
        if (session?.id === id) {
          setSession(null);
          clearSessionState();
          navigateHome("sessions");
        }
        await refreshSessions();
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [session, clearSessionState, navigateHome, refreshSessions],
  );

  const handleUpload = useCallback(
    async (uploaded: File[]) => {
      if (uploaded.length === 0 || !session) return;
      const seen = new Set<string>();
      const deduped = uploaded.filter((f) => {
        const key = `${f.name}:${f.size}:${f.lastModified}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      if (deduped.length === 0) return;
      setError(null);
      setUploading(true);
      trackEvent("cerno_upload_selected", {
        session_id: session.id,
        file_count: deduped.length,
        total_bytes: deduped.reduce((sum, file) => sum + file.size, 0),
      });
      setActiveTab("insights");
      navigateSessionTab(session.id, "insights");
      try {
        await uploadFiles(session.id, deduped);
        trackEvent("cerno_upload_completed", {
          session_id: session.id,
          file_count: deduped.length,
          total_bytes: deduped.reduce((sum, file) => sum + file.size, 0),
        });
        setEvents([]);
        setProcessing(false);
        setDiscovery(null);
        setLinks([]);
        setDataDoc(null);
        await Promise.all([
          refreshFiles(session.id),
          refreshSessions(),
        ]);
        await queryClient.invalidateQueries({ queryKey: ["workspace", session.id] });
      } catch (err) {
        trackEvent("cerno_upload_failed", {
          session_id: session.id,
          file_count: deduped.length,
        });
        setError((err as Error).message);
        await refreshDiscovery(session.id).catch(() => undefined);
      } finally {
        setUploading(false);
      }
    },
    [
      session,
      navigateSessionTab,
      refreshFiles,
      refreshSessions,
      refreshDiscovery,
      queryClient,
    ],
  );

  const handleDeleteFile = useCallback(
    async (fileId: string) => {
      if (!session) return;
      setError(null);
      setActiveTab("insights");
      navigateSessionTab(session.id, "insights");
      try {
        await deleteFile(fileId);
        const nextFiles = await listFiles(session.id);
        setFiles(nextFiles);
        setDiscovery(null);
        setLinks([]);
        setDataDoc(null);
        setEvents([]);
        await refreshSessions();
        await queryClient.invalidateQueries({ queryKey: ["workspace", session.id] });
      } catch (err) {
        setError((err as Error).message);
        await refreshDiscovery(session.id).catch(() => undefined);
        await refreshEvents(session.id).catch(() => undefined);
      }
    },
    [
      session,
      navigateSessionTab,
      refreshEvents,
      refreshSessions,
      refreshDiscovery,
      queryClient,
    ],
  );

  const handleProcess = useCallback(async () => {
    if (!session) return;
    setError(null);
    setProcessing(true);
    setEvents([]);
    trackEvent("cerno_schema_process_started", { session_id: session.id });
    setActiveTab("insights");
    navigateSessionTab(session.id, "insights");
    try {
      const result = await processSession(session.id);
      trackEvent("cerno_schema_process_queued", {
        session_id: session.id,
        job_id: result.job_id,
      });
      setEvents(result.events);
      await Promise.all([
        refreshFiles(session.id),
        refreshDiscovery(session.id),
        refreshEvents(session.id),
        refreshSessions(),
      ]);
      await queryClient.invalidateQueries({ queryKey: ["workspace", session.id] });
    } catch (err) {
      trackEvent("cerno_schema_process_failed", { session_id: session.id });
      setError((err as Error).message);
      await refreshDiscovery(session.id).catch(() => undefined);
      await refreshEvents(session.id).catch(() => undefined);
      setProcessing(false);
    }
  }, [
    session,
    navigateSessionTab,
    refreshFiles,
    refreshDiscovery,
    refreshEvents,
    refreshSessions,
    queryClient,
  ]);

  const handleApprove = useCallback(
    async (
      filesPayload: DiscoveredFile[],
      linksPayload: DiscoveredLink[],
      overview: string,
    ) => {
      if (!session) return;
      setError(null);
      setApproving(true);
      trackEvent("cerno_schema_approval_started", { session_id: session.id });
      try {
        const result = await approveSchema(session.id, {
          files: filesPayload,
          links: linksPayload,
          overview,
        });
        setDiscovery(result);
        await Promise.all([
          refreshFiles(session.id),
          refreshLinks(session.id),
          refreshSchemaGuide(session.id),
          refreshSessions(),
        ]);
        await queryClient.invalidateQueries({ queryKey: ["workspace", session.id] });
        setActiveTab("ask");
        navigateSessionTab(session.id, "ask");
        trackEvent("cerno_schema_approved", { session_id: session.id });
      } catch (err) {
        trackEvent("cerno_schema_approval_failed", { session_id: session.id });
        setError((err as Error).message);
      } finally {
        setApproving(false);
      }
    },
    [
      session,
      navigateSessionTab,
      refreshFiles,
      refreshLinks,
      refreshSchemaGuide,
      refreshSessions,
      queryClient,
    ],
  );

  const handleSend = useCallback(
    async (message: string) => {
      if (!session) return;
      setError(null);
      setSending(true);
      trackEvent("cerno_chat_sent", {
        session_id: session.id,
        existing_turn: Boolean(activeTurnId),
      });
      setLiveChat({
        turnId: activeTurnId,
        userMessage: message,
        assistantText: "",
        reasoningText: "",
        tools: [],
        error: null,
      });
      try {
        await streamChat(
          session.id,
          { message, turnId: activeTurnId },
          (event: ChatStreamEvent) => {
            if (event.type === "turn_started") {
              setActiveTurnId(event.turn_id);
              setLiveChat((current) =>
                current ? { ...current, turnId: event.turn_id } : current,
              );
              return;
            }
            if (event.type === "assistant_delta") {
              setLiveChat((current) =>
                current
                  ? { ...current, assistantText: current.assistantText + event.delta }
                  : current,
              );
              return;
            }
            if (event.type === "reasoning_delta") {
              setLiveChat((current) =>
                current
                  ? { ...current, reasoningText: current.reasoningText + event.delta }
                  : current,
              );
              return;
            }
            if (event.type === "tool_call_started") {
              setLiveChat((current) =>
                current
                  ? {
                      ...current,
                      tools: [
                        ...current.tools,
                        { callId: event.call_id, name: event.name, argsText: "" },
                      ],
                    }
                  : current,
              );
              return;
            }
            if (event.type === "tool_call_arguments_delta") {
              setLiveChat((current) =>
                current
                  ? {
                      ...current,
                      tools: current.tools.map((tool) =>
                        tool.callId === event.call_id
                          ? { ...tool, argsText: tool.argsText + event.delta }
                          : tool,
                      ),
                    }
                  : current,
              );
              return;
            }
            if (event.type === "tool_call_done") {
              setLiveChat((current) =>
                current
                  ? {
                      ...current,
                      tools: current.tools.map((tool) =>
                        tool.callId === event.call_id
                          ? { ...tool, name: event.name, args: event.arguments }
                          : tool,
                      ),
                    }
                  : current,
              );
              return;
            }
            if (event.type === "tool_result") {
              setLiveChat((current) =>
                current
                  ? {
                      ...current,
                      tools: current.tools.map((tool) =>
                        tool.callId === event.call_id
                          ? { ...tool, name: event.name, result: event.result }
                          : tool,
                      ),
                    }
                  : current,
              );
              return;
            }
            if (event.type === "done") {
              setActiveTurnId(event.turn_id);
              setLiveChat((current) =>
                current
                  ? {
                      ...current,
                      turnId: event.turn_id,
                      assistantText: event.assistant_message || current.assistantText,
                    }
                  : current,
              );
              return;
            }
            if (event.type === "error") {
              const message = event.message || USER_SAFE_CHAT_ERROR;
              setLiveChat((current) =>
                current ? { ...current, turnId: event.turn_id || current.turnId, error: message } : current,
              );
              setError(message);
            }
          },
        );
        await refreshChatFeed(session.id);
        await queryClient.invalidateQueries({ queryKey: ["workspace", session.id] });
        setLiveChat(null);
        trackEvent("cerno_chat_completed", { session_id: session.id });
      } catch (err) {
        const message =
          err instanceof Error && err.message === "unauthorized"
            ? err.message
            : USER_SAFE_CHAT_ERROR;
        setError(message);
        setLiveChat((current) => (current ? { ...current, error: message } : current));
        trackEvent("cerno_chat_failed", { session_id: session.id });
      } finally {
        setSending(false);
      }
    },
    [activeTurnId, session, refreshChatFeed, queryClient],
  );

  const handleNewChat = useCallback(() => {
    if (session) navigateSessionTab(session.id, "ask");
    setActiveTab("ask");
    setActiveTurnId(null);
    setLiveChat(null);
  }, [navigateSessionTab, session]);

  const handleSelectTurn = useCallback(
    (turnId: string) => {
      if (session) navigateSessionTab(session.id, "ask");
      setActiveTab("ask");
      setActiveTurnId(turnId);
    },
    [navigateSessionTab, session],
  );

  const handleRenameTurn = useCallback(
    async (turnId: string, title: string) => {
      if (!session) return;
      setError(null);
      try {
        await updateTurn(turnId, { title });
        await refreshChatFeed(session.id);
        await queryClient.invalidateQueries({ queryKey: ["workspace", session.id] });
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [session, refreshChatFeed, queryClient],
  );

  const handleDeleteTurn = useCallback(
    async (turnId: string) => {
      if (!session) return;
      setError(null);
      try {
        await deleteTurn(turnId);
        if (activeTurnId === turnId) setActiveTurnId(null);
        await refreshChatFeed(session.id);
        await queryClient.invalidateQueries({ queryKey: ["workspace", session.id] });
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [activeTurnId, session, refreshChatFeed, queryClient],
  );

  const handleTabChange = useCallback(
    (tab: TabKey) => {
      if (session) navigateSessionTab(session.id, tab);
      startNavigationTransition(() => {
        setActiveTab(tab);
      });
    },
    [navigateSessionTab, session, startNavigationTransition],
  );

  const discoveryStatus = discovery?.status ?? "empty";
  const sessionHasWorkspaceContent =
    files.length > 0 || discoveryStatus !== "empty" || session?.status !== "new";
  const workspaceLoading =
    Boolean(session) &&
    (workspaceQuery.isPending ||
      (workspaceQuery.isFetching && files.length === 0 && sessionHasWorkspaceContent));
  const showSessionTabs = sessionHasWorkspaceContent || workspaceLoading;

  if (!session) {
    if (homeView === "landing") {
      return <LandingPage onEnter={() => navigateHome("sessions")} />;
    }
    if (route.kind === "owner") {
      return (
        <AuthGate>
          {(user, signOut, onUserUpdate) => (
            <BetaGate user={user} onUserUpdate={onUserUpdate}>
              <OwnerDashboardPage
                user={user}
                onBack={() => navigateHome("sessions")}
                onOpenOrganizationAdmin={(organizationId) =>
                  navigate({ kind: "org-admin", organizationId })
                }
                onSignOut={async () => {
                  trackEvent("cerno_user_signed_out", { surface: "owner_dashboard" });
                  await signOut();
                  navigateHome("landing");
                }}
              />
            </BetaGate>
          )}
        </AuthGate>
      );
    }
    if (route.kind === "org-admin") {
      return (
        <AuthGate>
          {(user, signOut, onUserUpdate) => (
            <BetaGate user={user} onUserUpdate={onUserUpdate}>
              <OrganizationAdminDashboardPage
                organizationId={route.organizationId}
                user={user}
                onBack={() => navigateHome("sessions")}
                onSignOut={async () => {
                  trackEvent("cerno_user_signed_out", { surface: "org_admin_dashboard" });
                  await signOut();
                  navigateHome("landing");
                }}
              />
            </BetaGate>
          )}
        </AuthGate>
      );
    }
    return (
      <AuthGate>
        {(user, signOut, onUserUpdate) => (
          <BetaGate user={user} onUserUpdate={onUserUpdate}>
            <WorkspacesPage
              user={user}
              sessions={sessions}
              onStart={handleCreateSession}
              onResume={handleResumeSession}
              onDelete={handleDeleteSession}
              onPrefetch={prefetchWorkspace}
              metrics={workspaceMetrics}
              starting={starting}
              error={error}
              onDismissError={() => setError(null)}
              onBackToLanding={() => navigateHome("landing")}
              onOpenOwnerDashboard={() => navigate({ kind: "owner" })}
              onOpenOrganizationAdmin={(organizationId) =>
                navigate({ kind: "org-admin", organizationId })
              }
              onSignOut={async () => {
                trackEvent("cerno_user_signed_out", { surface: "workspaces" });
                await signOut();
                navigateHome("landing");
              }}
            />
          </BetaGate>
        )}
      </AuthGate>
    );
  }

  const chatReady = discoveryStatus === "approved";

  return (
    <AuthGate>
      {(user, _signOut, onUserUpdate) => (
        <BetaGate user={user} onUserUpdate={onUserUpdate}>
          <Shell
            sidebar={
          <WorkspaceSidebar
            session={session}
            files={files}
            turns={turns}
            messagesByTurn={messagesByTurn}
            activeTurnId={activeTurnId}
            onSelectTab={handleTabChange}
            onSelectTurn={handleSelectTurn}
            onNewChat={handleNewChat}
            onRenameTurn={handleRenameTurn}
            onDeleteTurn={handleDeleteTurn}
          />
        }
        header={
          <SessionHeader
            session={session}
            sessions={sessions}
            onUpload={handleUpload}
            uploading={uploading}
            onHome={handleHome}
            onDelete={() => handleDeleteSession(session.id)}
            onResume={handleResumeSession}
            onPrefetch={prefetchWorkspace}
            showFileActions={showSessionTabs}
          />
        }
        activeTab={activeTab}
        onTabChange={handleTabChange}
        onDropFile={handleUpload}
        showTabs={showSessionTabs}
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((current) => !current)}
      >
        {workspaceLoading ? (
          <WorkspaceLoadingPanel activeTab={activeTab} />
        ) : files.length === 0 ? (
          <FilesPanel
            files={files}
            discoveryStatus={discoveryStatus}
            uploading={uploading}
            processing={processing}
            onUpload={handleUpload}
            onDeleteFile={handleDeleteFile}
            onProcess={handleProcess}
          />
        ) : null}
        {!workspaceLoading && files.length > 0 && activeTab === "ask" ? (
          <ChatSidebar
            turns={turns}
            messagesByTurn={messagesByTurn}
            artifactsByTurn={artifactsByTurn}
            activeTurnId={activeTurnId}
            files={files}
            discovery={discovery}
            dataDoc={dataDoc}
            discoveryStatus={discoveryStatus}
            disabled={!chatReady}
            sending={sending}
            liveChat={liveChat}
            onSend={handleSend}
            onOpenInsights={() => handleTabChange("insights")}
          />
        ) : null}
        {!workspaceLoading && files.length > 0 && activeTab === "insights" ? (
          <SchemaTab
            files={files}
            links={links}
            discovery={discovery}
            doc={dataDoc}
            events={events}
            processing={processing}
            approving={approving}
            canProcess={files.length > 0}
            onProcess={handleProcess}
            onApprove={handleApprove}
            onDeleteFile={handleDeleteFile}
          />
        ) : null}
      </Shell>
      {error ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-4 flex justify-center">
          <div className="pointer-events-auto border border-night-watch bg-tidepaper px-3 py-2 font-mono text-xs text-red-600">
            {error}
            <button
              type="button"
              onClick={() => setError(null)}
              className="ml-3 text-neutral-500 hover:text-night-watch"
            >
              {"\u00d7"}
            </button>
          </div>
        </div>
      ) : null}
        </BetaGate>
      )}
    </AuthGate>
  );
}

type Point = {
  x: number;
  y: number;
};

type ContourIsland = {
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  levels: number;
  phase: number;
  rotate: number;
  tightness: number;
};

type TopographicContour = {
  path: string;
  islandIndex: number;
  level: number;
};

function formatPoint(point: Point) {
  return `${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
}

function buildSmoothClosedPath(points: Point[]) {
  const pointAt = (index: number) => points[(index + points.length) % points.length] as Point;
  const segments = points.map((point, idx) => {
    const previous = pointAt(idx - 1);
    const next = pointAt(idx + 1);
    const afterNext = pointAt(idx + 2);
    const controlOne = {
      x: point.x + (next.x - previous.x) / 6,
      y: point.y + (next.y - previous.y) / 6,
    };
    const controlTwo = {
      x: next.x - (afterNext.x - point.x) / 6,
      y: next.y - (afterNext.y - point.y) / 6,
    };

    return `C${formatPoint(controlOne)} ${formatPoint(controlTwo)} ${formatPoint(next)}`;
  });

  return `M${formatPoint(pointAt(0))} ${segments.join(" ")}Z`;
}

function buildContourPath(island: ContourIsland, level: number) {
  const scale = 1 - level * island.tightness;
  const rotation = (island.rotate * Math.PI) / 180;
  const points = Array.from({ length: 28 }, (_, idx) => {
    const angle = (Math.PI * 2 * idx) / 28;
    const radial =
      1 +
      Math.sin(angle * 2 + island.phase) * 0.08 +
      Math.sin(angle * 3 - island.phase * 0.72) * 0.055 +
      Math.cos(angle * 5 + island.phase * 1.4) * 0.035;
    const rawX = Math.cos(angle) * island.rx * scale * radial;
    const rawY = Math.sin(angle) * island.ry * scale * radial;

    return {
      x: island.cx + rawX * Math.cos(rotation) - rawY * Math.sin(rotation),
      y: island.cy + rawX * Math.sin(rotation) + rawY * Math.cos(rotation),
    };
  });

  return buildSmoothClosedPath(points);
}

const topographicIslands: ContourIsland[] = [
  { cx: 890, cy: 360, rx: 355, ry: 244, levels: 13, phase: 0.35, rotate: -8, tightness: 0.062 },
];

const topographicContours: TopographicContour[] = topographicIslands.flatMap((island, islandIndex) =>
  Array.from({ length: island.levels }, (_, level) => ({
    path: buildContourPath(island, level),
    islandIndex,
    level,
  })),
);

const surveyLabels = [
  { text: "04", className: "right-[31%] top-[21%]" },
  { text: "88", className: "right-[15%] top-[43%]" },
];

const landingWorkflow = [
  {
    index: "01",
    title: "Profile files",
    body: "Reads Excel and CSV files, detects sheets, columns, row counts, types, and header issues.",
    icon: FileSpreadsheet,
  },
  {
    index: "02",
    title: "Review schema",
    body: "Creates field descriptions, relationships, caveats, glossary terms, and starter questions.",
    icon: Database,
  },
  {
    index: "03",
    title: "Ask questions",
    body: "Answers with tables, charts, and saved views using the approved workspace context.",
    icon: MessageSquare,
  },
];

const contextRows = [
  { label: "Uploaded files", value: "Source files, row counts, sheet names, and upload history.", icon: Table2 },
  { label: "Approved schema", value: "Field meanings, relationships, caveats, and glossary terms.", icon: ShieldCheck },
  { label: "Chat history", value: "Follow-up questions stay in the same workspace thread.", icon: MessageSquare },
];

function LandingPage({
  onEnter,
}: {
  onEnter: () => void;
}) {
  const { user, loading } = useUser();
  const pointerX = useMotionValue(0);
  const pointerY = useMotionValue(0);
  const smoothX = useSpring(pointerX, { stiffness: 80, damping: 24, mass: 0.4 });
  const smoothY = useSpring(pointerY, { stiffness: 80, damping: 24, mass: 0.4 });
  const topoX = useTransform(smoothX, [-0.5, 0.5], [-26, 26]);
  const topoY = useTransform(smoothY, [-0.5, 0.5], [-18, 18]);

  useEffect(() => {
    if (!loading && user && user.access_status === "granted") {
      onEnter();
    }
  }, [user, loading, onEnter]);

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const rect = event.currentTarget.getBoundingClientRect();
      pointerX.set((event.clientX - rect.left) / rect.width - 0.5);
      pointerY.set((event.clientY - rect.top) / rect.height - 0.5);
    },
    [pointerX, pointerY],
  );

  if (loading || (user && user.access_status === "granted")) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#F2EBDD] text-[#141210]">
        Loading...
      </div>
    );
  }

  return (
    <div
      className="cerno-paper-grain relative min-h-full overflow-x-hidden bg-[#F2EBDD] text-[#141210]"
      onPointerMove={handlePointerMove}
    >
      <main className="relative">
        <header className="absolute inset-x-0 top-0 z-30 flex items-center justify-between border-b border-[#141210]/10 bg-[#F2EBDD]/72 px-6 py-5 backdrop-blur-md sm:px-10">
          <CernoLockup markClassName="h-6 w-6" wordmarkClassName="text-base text-[#141210]" />
          <a
            href={googleLoginUrl()}
            className="small-caps border border-[#141210]/20 bg-[#F2EBDD]/70 px-3 py-1.5 text-xs text-[#141210] transition hover:border-[#D17B2E] hover:text-[#943B18]"
          >
            login
          </a>
        </header>

        <section className="relative isolate min-h-[100svh] overflow-hidden px-6 pb-12 pt-28 sm:px-10">
          <OceanTopographyBackground
            topoX={topoX}
            topoY={topoY}
          />
          <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(242,235,221,0.99)_0%,rgba(242,235,221,0.93)_32%,rgba(242,235,221,0.48)_61%,rgba(242,235,221,0.14)_100%)]" />
          <div className="absolute inset-x-0 bottom-0 h-36 bg-gradient-to-t from-[#F2EBDD] to-transparent" />

          <div className="relative z-10 mx-auto flex min-h-[calc(100svh-10rem)] max-w-7xl items-center">
            <motion.div
              initial={{ opacity: 0, y: 18 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, ease: "easeOut" }}
              className="w-full max-w-3xl"
            >
              <div className="small-caps text-xs text-[#176B7D]">
                private beta
              </div>
              <h1 className="mt-6 font-serif text-7xl leading-[0.9] text-[#141210] sm:text-8xl lg:text-9xl">
                Cerno
              </h1>
              <p className="mt-7 max-w-2xl font-serif text-4xl leading-tight text-[#141210] sm:text-5xl">
                Turn spreadsheets into trusted analysis.
              </p>
              <p className="mt-6 max-w-xl text-base leading-7 text-[#141210]/68 sm:text-lg sm:leading-8">
                Upload Excel or CSV files, review the generated schema, then ask
                questions with context Cerno can verify.
              </p>

              <motion.div
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.25, duration: 0.7, ease: "easeOut" }}
                className="mt-10 flex flex-col items-start gap-3 sm:flex-row sm:items-center"
              >
                <a
                  href={googleLoginUrl()}
                  className="group flex items-center justify-center gap-2 bg-[#D17B2E] px-6 py-4 small-caps text-sm text-[#141210] shadow-[0_18px_46px_rgba(184,82,31,0.24)] transition hover:bg-[#E89A48]"
                >
                  sign in to create a workspace
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                </a>
                <span className="font-mono text-xs text-[#141210]/48">
                  Excel and CSV files only.
                </span>
              </motion.div>

              <div className="mt-10 grid max-w-xl grid-cols-3 border-y border-[#141210]/15">
                <LandingMeasure value="files" label="Excel / CSV" />
                <LandingMeasure value="schema" label="review step" />
                <LandingMeasure value="chat" label="grounded answers" />
              </div>
            </motion.div>
          </div>
        </section>

        <section className="relative overflow-hidden border-y border-[#CBC1AD]/70 bg-[#F8F0E2] px-6 py-16 sm:px-10">
          <MiniTopoStrip />
          <div className="relative z-10 mx-auto grid max-w-7xl gap-12 lg:grid-cols-[minmax(18rem,0.7fr)_minmax(0,1.3fr)]">
            <div>
              <div className="small-caps text-xs text-[#176B7D]">workflow</div>
              <h2 className="mt-3 max-w-sm font-serif text-4xl leading-tight text-[#141210]">
                What Cerno does.
              </h2>
            </div>
            <div className="border-t border-[#141210]/15">
              {landingWorkflow.map((item, idx) => (
                <WorkflowRow key={item.index} {...item} delay={idx * 0.08} />
              ))}
            </div>
          </div>
        </section>

        <section className="relative overflow-hidden bg-[#07242E] px-6 py-16 text-[#F2EBDD] sm:px-10">
          <OceanTopographyBackground dark />
          <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(7,36,46,0.98)_0%,rgba(7,36,46,0.88)_48%,rgba(7,36,46,0.74)_100%)]" />
          <div className="relative z-10 mx-auto grid max-w-7xl gap-10 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
            <div>
              <div className="small-caps text-xs text-[#E89A48]">workspace context</div>
              <h2 className="mt-3 max-w-2xl font-serif text-4xl leading-tight text-[#F2EBDD] sm:text-5xl">
                Keep the important context attached.
              </h2>
              <p className="mt-5 max-w-xl text-base leading-7 text-[#F2EBDD]/68">
                Cerno stores the files, approved schema, and chat history together
                so later questions use the same reviewed context.
              </p>
            </div>
            <div className="border-t border-[#F2EBDD]/18">
              {contextRows.map((item, idx) => (
                <ContextRow key={item.label} {...item} delay={idx * 0.08} />
              ))}
            </div>
          </div>
        </section>

        <section className="border-t border-[#CBC1AD]/70 bg-[#F2EBDD] px-6 py-12 sm:px-10">
          <div className="mx-auto flex max-w-7xl flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="small-caps text-xs text-[#176B7D]">private beta</div>
              <h2 className="mt-2 max-w-2xl font-serif text-3xl leading-tight text-[#141210]">
                Start with a workbook.
              </h2>
            </div>
            <a
              href={googleLoginUrl()}
              className="group flex w-fit items-center gap-2 bg-[#141210] px-5 py-3 small-caps text-sm text-[#F2EBDD] transition hover:bg-[#D17B2E] hover:text-[#141210]"
            >
              login
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
            </a>
          </div>
        </section>
      </main>
    </div>
  );
}

function OceanTopographyBackground({
  dark = false,
  topoX,
  topoY,
}: {
  dark?: boolean;
  topoX?: MotionValue<number>;
  topoY?: MotionValue<number>;
}) {
  const contourColor = dark ? "#EBF0EC" : "#07242E";
  const kaiColor = dark ? "#72BCC9" : "#1F869A";
  const orangeColor = dark ? "#E89A48" : "#D17B2E";
  const topoStyle = topoX && topoY ? { x: topoX, y: topoY } : undefined;

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            dark
              ? "radial-gradient(circle at 78% 42%, rgba(62,160,178,0.12), transparent 42%), radial-gradient(circle at 84% 70%, rgba(209,123,46,0.10), transparent 34%)"
              : "radial-gradient(circle at 76% 42%, rgba(31,134,154,0.13), transparent 42%), radial-gradient(circle at 86% 72%, rgba(209,123,46,0.12), transparent 34%)",
        }}
      />
      <motion.svg
        viewBox="0 0 1200 820"
        preserveAspectRatio="xMidYMid slice"
        className="absolute inset-[-9%] h-[118%] w-[118%]"
        aria-hidden="true"
        style={topoStyle}
      >
        <g fill="none" strokeLinecap="round" strokeLinejoin="round">
          {topographicContours
            .filter((contour) => contour.islandIndex === 0 && contour.level === 5)
            .map((contour) => (
              <path
                key={`fill-${contour.path}`}
                d={contour.path}
                fill={orangeColor}
                opacity={dark ? 0.12 : 0.08}
                stroke="none"
              />
            ))}
          {topographicContours.map((contour, idx) => {
            const highlighted = contour.islandIndex === 0 && (contour.level === 2 || contour.level === 8);
            const oceanLine = contour.level % 4 === 0;
            const stroke = highlighted ? orangeColor : oceanLine ? kaiColor : contourColor;
            const strokeWidth = highlighted ? 2.4 : oceanLine ? 1.75 : 1.15;
            const opacity = highlighted
              ? dark ? 0.72 : 0.68
              : dark ? oceanLine ? 0.42 : 0.28
                : oceanLine ? 0.38 : 0.26;

            return (
              <motion.path
                key={contour.path}
                d={contour.path}
                stroke={stroke}
                strokeWidth={strokeWidth}
                opacity={opacity}
                initial={{ pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={{ duration: 1.1, delay: idx * 0.035, ease: "easeOut" }}
              />
            );
          })}
        </g>
        <g>
          {topographicIslands.slice(0, 2).map((island, idx) => (
            <motion.path
              key={`${island.cx}-${island.cy}`}
              d={buildContourPath(island, island.levels - 1)}
              fill={orangeColor}
              initial={{ scale: 0.92, opacity: 0 }}
              animate={{ scale: [0.92, 1, 0.96, 1], opacity: [0, 0.72, 0.45, 0.72] }}
              style={{ transformOrigin: `${island.cx}px ${island.cy}px` }}
              transition={{ delay: 0.55 + idx * 0.16, duration: 3.2, repeat: Infinity, repeatDelay: 2.6 }}
            />
          ))}
        </g>
      </motion.svg>

      {surveyLabels.map((label) => (
        <span
          key={label.text}
          className={`absolute hidden font-mono text-xs ${dark ? "text-[#EBF0EC]/35" : "text-[#07242E]/30"} lg:block ${label.className}`}
        >
          {label.text}
        </span>
      ))}
    </div>
  );
}

function LandingMeasure({ value, label }: { value: string; label: string }) {
  return (
    <div className="border-r border-[#141210]/15 py-4 pr-3 last:border-r-0 last:pl-4 sm:px-4 sm:first:pl-0">
      <div className="font-mono text-lg text-[#141210]">{value}</div>
      <div className="small-caps mt-1 text-[11px] text-[#141210]/45">{label}</div>
    </div>
  );
}

function MiniTopoStrip() {
  return (
    <div className="pointer-events-none absolute inset-y-0 right-0 w-[55%] opacity-35">
      <svg viewBox="0 0 620 420" preserveAspectRatio="xMidYMid slice" className="h-full w-full">
        <g fill="none" stroke="#176B7D" strokeLinecap="round" strokeLinejoin="round">
          {topographicContours
            .filter((contour) => contour.islandIndex === 0)
            .slice(1, 11)
            .map((contour, idx) => (
            <path
              key={`mini-${contour.path}`}
              d={contour.path}
              strokeWidth={idx % 3 === 0 ? 2 : 1.2}
              opacity={0.42}
              transform="translate(-520 -158) scale(0.92)"
            />
          ))}
          <path
            d={topographicContours.find((contour) => contour.islandIndex === 0 && contour.level === 4)?.path}
            stroke="#D17B2E"
            strokeWidth="2.2"
            opacity="0.62"
            transform="translate(-520 -158) scale(0.92)"
          />
        </g>
      </svg>
    </div>
  );
}

function WorkflowRow({
  index,
  title,
  body,
  icon: Icon,
  delay = 0,
}: {
  index: string;
  title: string;
  body: string;
  icon: LucideIcon;
  delay?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.45 }}
      whileHover={{ x: 8 }}
      transition={{ duration: 0.45, delay }}
      className="group grid gap-5 border-b border-[#141210]/15 py-7 transition-colors sm:grid-cols-[6rem_minmax(0,1fr)_2.5rem]"
    >
      <div className="font-mono text-sm text-[#D17B2E]">{index}</div>
      <div>
        <h3 className="font-mono text-xl text-[#141210]">{title}</h3>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-[#141210]/68">{body}</p>
      </div>
      <Icon className="h-6 w-6 text-[#176B7D] transition group-hover:text-[#D17B2E] sm:justify-self-end" />
    </motion.div>
  );
}

function ContextRow({
  label,
  value,
  icon: Icon,
  delay = 0,
}: {
  label: string;
  value: string;
  icon: LucideIcon;
  delay?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.45 }}
      whileHover={{ x: 8 }}
      transition={{ duration: 0.45, delay }}
      className="group grid gap-5 border-b border-[#F2EBDD]/18 py-6 sm:grid-cols-[2.5rem_minmax(10rem,0.45fr)_minmax(0,1fr)]"
    >
      <Icon className="h-5 w-5 text-[#72BCC9] transition group-hover:text-[#E89A48]" />
      <div className="font-mono text-base text-[#F2EBDD]">{label}</div>
      <div className="text-sm leading-6 text-[#F2EBDD]/68">{value}</div>
    </motion.div>
  );
}

function WorkspacesPage({
  user,
  sessions,
  onStart,
  onResume,
  onDelete,
  onPrefetch,
  metrics,
  starting,
  error,
  onDismissError,
  onBackToLanding,
  onOpenOwnerDashboard,
  onOpenOrganizationAdmin,
  onSignOut,
}: {
  user: CurrentUser;
  sessions: Session[];
  onStart: (name: string) => void;
  onResume: (session: Session) => void;
  onDelete: (id: string) => void;
  onPrefetch: (sessionId: string) => void;
  metrics: Record<string, WorkspaceMetric>;
  starting: boolean;
  error: string | null;
  onDismissError: () => void;
  onBackToLanding: () => void;
  onOpenOwnerDashboard: () => void;
  onOpenOrganizationAdmin: (organizationId: string) => void;
  onSignOut: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const stats = buildWorkspaceStats(sessions, metrics);
  const pointerX = useMotionValue(0);
  const pointerY = useMotionValue(0);
  const topoX = useSpring(useTransform(pointerX, [-0.5, 0.5], [-18, 18]), {
    stiffness: 70,
    damping: 24,
    mass: 0.7,
  });
  const topoY = useSpring(useTransform(pointerY, [-0.5, 0.5], [-12, 12]), {
    stiffness: 70,
    damping: 24,
    mass: 0.7,
  });

  useEffect(() => {
    sessions.slice(0, 6).forEach((session) => onPrefetch(session.id));
  }, [onPrefetch, sessions]);

  const handleStart = () => {
    const trimmed = name.trim();
    const fallback = `workspace ${new Date().toLocaleDateString()}`;
    onStart(trimmed.length > 0 ? trimmed : fallback);
  };

  const handleKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !starting) handleStart();
  };

  const handleDelete = (s: Session) => {
    const ok = window.confirm(
      `Delete workspace "${s.name}"? Files, links, insights, and chat history will be removed. This cannot be undone.`,
    );
    if (ok) onDelete(s.id);
  };

  return (
    <div
      className="cerno-paper-grain relative flex min-h-full w-full overflow-hidden bg-[#F2EBDD] px-6 py-8 text-[#141210] sm:px-8 lg:px-10"
      onMouseMove={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        pointerX.set((event.clientX - rect.left) / rect.width - 0.5);
        pointerY.set((event.clientY - rect.top) / rect.height - 0.5);
      }}
    >
      <WorkspacesCartographyBackground topoX={topoX} topoY={topoY} />
      <main className="relative z-10 mx-auto flex w-full max-w-7xl flex-col py-3">
        <header className="flex items-center justify-between gap-4 border-b border-[#141210]/12 pb-4">
          <button
            type="button"
            onClick={onBackToLanding}
            className="text-[#141210]/62 transition hover:text-[#176B7D]"
          >
            <CernoLockup markClassName="h-6 w-6" wordmarkClassName="text-base" />
          </button>
          <div className="small-caps text-sm text-[#141210]/48">workspaces</div>
        </header>

        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="min-w-0">
            <section className="mt-14 grid gap-10 lg:grid-cols-[minmax(0,1.05fr)_minmax(24rem,0.95fr)]">
              <motion.div
                initial={{ opacity: 0, y: 18 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.65, ease: "easeOut" }}
              >
                <div className="small-caps text-sm text-[#176B7D]">workspaces</div>
                <h1 className="mt-3 max-w-4xl font-serif text-5xl leading-[0.94] tracking-tight text-[#141210] sm:text-6xl">
                  Open a workspace or start with new files.
                </h1>
                <p className="mt-6 max-w-2xl text-lg leading-8 text-[#141210]/64">
                  Upload Excel or CSV files, review the generated schema, then ask questions with context Cerno can verify.
                </p>
              </motion.div>

              <motion.div
                initial={{ opacity: 0, y: 18 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.65, delay: 0.08, ease: "easeOut" }}
                className="relative overflow-hidden border border-[#141210]/12 bg-[#F6EFE2]/70 px-5 py-7 shadow-[0_18px_60px_rgba(20,18,16,0.05)] backdrop-blur-[1px]"
              >
                <MiniTopoStrip />
                <div className="relative z-10 grid grid-cols-2 gap-x-10 gap-y-7">
                <WorkspaceStat label="workspaces" value={stats.workspaces} />
                <WorkspaceStat label="files uploaded" value={stats.filesUploaded} />
                <WorkspaceStat label="last activity" value={stats.lastActivity} />
                <WorkspaceStat label="total rows" value={stats.totalRows} />
                </div>
              </motion.div>
            </section>

            <motion.section
              className="mx-auto mt-12 w-full max-w-5xl"
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.62, delay: 0.16, ease: "easeOut" }}
            >
              <div className="w-full border border-[#141210]/12 bg-[#F7F1E7]/78 p-2 shadow-[0_18px_50px_rgba(20,18,16,0.04)]">
                <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_14rem]">
                  <label className="grid min-w-0 gap-2 border border-[#141210]/10 bg-[#F2EBDD]/80 px-5 py-4 text-left text-[#141210] sm:grid-cols-[9.5rem_minmax(0,1fr)] sm:items-center">
                    <span className="small-caps text-sm text-[#141210]/50">
                      Workspace name
                    </span>
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      onKeyDown={handleKey}
                      placeholder="Monthly sales, audit data..."
                      className="min-w-0 bg-transparent font-mono text-base text-[#141210] placeholder:text-[#141210]/36 focus:outline-none"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={handleStart}
                    disabled={starting}
                    className="small-caps bg-[#D17B2E] px-7 py-4 text-sm text-[#141210] transition hover:bg-[#E89A48] disabled:opacity-40"
                  >
                    {starting ? "starting..." : "start workspace"}
                  </button>
                </div>
              </div>

              {error ? (
                <div className="mt-4 flex w-full items-start justify-between gap-3 border border-red-300 bg-red-50 px-3 py-2 font-mono text-xs text-red-600">
                  <span>{error}</span>
                  <button
                    type="button"
                    onClick={onDismissError}
                    className="text-red-500 hover:text-red-700"
                  >
                    x
                  </button>
                </div>
              ) : null}
            </motion.section>

            <motion.section
              className="mx-auto mt-14 w-full max-w-5xl"
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.62, delay: 0.24, ease: "easeOut" }}
            >
              <div className="flex items-end justify-between gap-4 border-b border-[#141210]/12 pb-4">
                <div>
                  <div className="small-caps text-sm text-[#176B7D]">current workspaces</div>
                  <h2 className="mt-2 font-serif text-2xl text-[#141210]">
                    Saved analysis rooms
                  </h2>
                </div>
                <span className="text-sm text-[#141210]/52">
                  {sessions.length} saved
                </span>
              </div>

              {sessions.length > 0 ? (
                <ul className="mt-5 grid gap-4">
                  {sessions.map((s) => (
                    <WorkspaceRow
                      key={s.id}
                      session={s}
                      metric={metrics[s.id]}
                      onResume={() => onResume(s)}
                      onDelete={() => handleDelete(s)}
                      onPrefetch={() => onPrefetch(s.id)}
                    />
                  ))}
                </ul>
              ) : (
                <div className="mt-5 border border-dashed border-[#141210]/16 bg-[#F7F1E7]/66 px-5 py-8 font-mono text-base text-[#141210]/56">
                  No saved workspaces yet.
                </div>
              )}
            </motion.section>
          </div>

          <UserProfilePanel
            user={user}
            onOpenOwnerDashboard={onOpenOwnerDashboard}
            onOpenOrganizationAdmin={onOpenOrganizationAdmin}
            onSignOut={onSignOut}
          />
        </div>
      </main>
    </div>
  );
}

function UserProfilePanel({
  user,
  onOpenOwnerDashboard,
  onOpenOrganizationAdmin,
  onSignOut,
}: {
  user: CurrentUser;
  onOpenOwnerDashboard: () => void;
  onOpenOrganizationAdmin: (organizationId: string) => void;
  onSignOut: () => Promise<void>;
}) {
  const displayName = userDisplayName(user);
  const adminOrganizations = user.organizations.filter((org) =>
    org.role === "owner" || org.role === "admin",
  );

  return (
    <motion.aside
      initial={{ opacity: 0, x: 18 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.62, delay: 0.18, ease: "easeOut" }}
      className="mt-14 h-fit border border-[#141210]/12 bg-[#F7F1E7]/74 p-4 shadow-[0_18px_50px_rgba(20,18,16,0.045)] backdrop-blur-[1px] lg:sticky lg:top-8"
    >
      <div className="flex items-center gap-3 border-b border-[#141210]/12 pb-4">
        {user.picture ? (
          <img
            src={user.picture}
            alt=""
            className="h-12 w-12 border border-[#141210]/14 object-cover"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="flex h-12 w-12 items-center justify-center border border-[#141210]/14 bg-[#F2EBDD] font-mono text-base text-[#176B7D]">
            {userInitials(displayName)}
          </div>
        )}
        <div className="min-w-0">
          <div className="truncate font-mono text-base text-[#141210]">{displayName}</div>
          <div className="truncate text-sm text-[#141210]/52">{user.email}</div>
        </div>
      </div>

      <section className="border-b border-[#141210]/12 py-5">
        <div className="small-caps text-sm text-[#141210]/52">usage stats</div>
        <div className="relative mt-4 h-24 overflow-hidden border border-dashed border-[#141210]/14 bg-[#F2EBDD]/62">
          <svg viewBox="0 0 260 120" preserveAspectRatio="none" className="absolute inset-0 h-full w-full opacity-70">
            <g fill="none" strokeLinecap="round" strokeLinejoin="round">
              {topographicContours.slice(2, 10).map((contour, idx) => (
                <path
                  key={`profile-${contour.path}`}
                  d={contour.path}
                  transform="translate(-725 -305) scale(0.62)"
                  stroke={idx % 3 === 0 ? "#D17B2E" : "#176B7D"}
                  strokeWidth={idx % 3 === 0 ? 2 : 1}
                  opacity={idx % 3 === 0 ? 0.34 : 0.2}
                />
              ))}
            </g>
          </svg>
        </div>
      </section>

      <div className="grid gap-2 pt-4">
        {user.site_role === "site_owner" ? (
          <button
            type="button"
            onClick={onOpenOwnerDashboard}
            className="small-caps border border-[#176B7D]/25 bg-[#F2EBDD] px-4 py-3 text-sm text-[#176B7D] transition hover:border-[#176B7D] hover:bg-[#C9E3E2]/20"
          >
            owner dashboard
          </button>
        ) : null}
        {adminOrganizations.map((org) => (
          <button
            key={org.id}
            type="button"
            onClick={() => onOpenOrganizationAdmin(org.id)}
            className="small-caps border border-[#176B7D]/25 bg-[#F2EBDD] px-4 py-3 text-sm text-[#176B7D] transition hover:border-[#176B7D] hover:bg-[#C9E3E2]/20"
          >
            {org.name} admin
          </button>
        ))}
        <button
          type="button"
          onClick={onSignOut}
          className="small-caps border border-red-900/20 bg-red-50 px-4 py-3 text-sm text-red-600 transition hover:border-red-900/40 hover:bg-red-100"
        >
          sign out
        </button>
      </div>
    </motion.aside>
  );
}

function OrganizationAdminDashboardPage({
  organizationId,
  user,
  onBack,
  onSignOut,
}: {
  organizationId: string;
  user: CurrentUser;
  onBack: () => void;
  onSignOut: () => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<OrganizationMemberBody["role"]>("member");
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const userOrg = user.organizations.find((org) => org.id === organizationId);
  const hasAccess =
    user.site_role === "site_owner" ||
    userOrg?.role === "owner" ||
    userOrg?.role === "admin";
  const dashboardQuery = useQuery({
    queryKey: ["organization", organizationId, "admin"],
    queryFn: () => getOrganizationAdminDashboard(organizationId),
    enabled: hasAccess,
    refetchInterval: 30_000,
  });
  const dashboard = dashboardQuery.data;

  useEffect(() => {
    if (hasAccess) trackEvent("cerno_org_admin_dashboard_opened", { organization_id: organizationId });
  }, [hasAccess, organizationId]);

  const refreshDashboard = async () => {
    trackEvent("cerno_org_admin_dashboard_refreshed", { organization_id: organizationId });
    await dashboardQuery.refetch();
  };

  const invalidateDashboard = async () => {
    await Promise.all([
      dashboardQuery.refetch(),
      queryClient.invalidateQueries({ queryKey: ["auth", "me"] }),
    ]);
  };

  const handleAddMember = async () => {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed) return;
    setActionError(null);
    setActionMessage(null);
    trackEvent("cerno_org_member_add_submitted", {
      organization_id: organizationId,
      role,
    });
    try {
      const result = await addOrganizationMember(organizationId, {
        email: trimmed,
        role,
      });
      setEmail("");
      setRole("member");
      setActionMessage(result.message ?? `Member ${result.status}.`);
      trackEvent("cerno_org_member_add_succeeded", {
        organization_id: organizationId,
        status: result.status,
        role,
      });
      await invalidateDashboard();
    } catch (err) {
      const message = (err as Error).message;
      setActionError(message);
      trackEvent("cerno_org_member_add_failed", {
        organization_id: organizationId,
        role,
      });
    }
  };

  const handleRoleChange = async (
    targetUserId: string,
    nextRole: OrganizationMemberBody["role"],
  ) => {
    setActionError(null);
    setActionMessage(null);
    try {
      await updateOrganizationMemberRole(organizationId, targetUserId, nextRole);
      setActionMessage("Member role updated.");
      trackEvent("cerno_org_member_role_updated", {
        organization_id: organizationId,
        role: nextRole,
      });
      await invalidateDashboard();
    } catch (err) {
      setActionError((err as Error).message);
    }
  };

  const handleRemoveMember = async (target: OrganizationAdminDashboard["users"][number]) => {
    const ok = window.confirm(`Remove ${target.email} from this organization?`);
    if (!ok) return;
    setActionError(null);
    setActionMessage(null);
    try {
      await removeOrganizationMember(organizationId, target.user_id);
      setActionMessage("Member removed from organization.");
      trackEvent("cerno_org_member_removed", { organization_id: organizationId });
      await invalidateDashboard();
    } catch (err) {
      setActionError((err as Error).message);
    }
  };

  if (!hasAccess) {
    return (
      <div className="flex min-h-full items-center justify-center bg-[#F2EBDD] px-6 text-[#141210]">
        <div className="max-w-md border border-red-900/20 bg-red-50 p-6">
          <div className="small-caps text-sm text-red-600">organization admin required</div>
          <p className="mt-3 text-sm leading-6 text-red-700">
            This dashboard is available to organization owners and admins only.
          </p>
          <button
            type="button"
            onClick={onBack}
            className="small-caps mt-5 border border-red-900/20 bg-white px-4 py-2 text-sm text-red-700"
          >
            back to workspaces
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="cerno-paper-grain min-h-full bg-[#F2EBDD] px-5 py-6 text-[#141210] sm:px-8 lg:px-10">
      <main className="mx-auto flex max-w-[92rem] flex-col gap-6">
        <header className="flex flex-col gap-4 border-b border-[#141210]/12 pb-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={onBack}
              className="text-[#141210]/64 transition hover:text-[#176B7D]"
              title="Back to workspaces"
            >
              <CernoLockup markClassName="h-6 w-6" wordmarkClassName="text-base" />
            </button>
            <div>
              <div className="small-caps text-sm text-[#176B7D]">organization admin</div>
              <h1 className="mt-1 font-serif text-4xl leading-none text-[#141210]">
                {dashboard?.organization.name ?? userOrg?.name ?? "Organization"}
              </h1>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="border border-[#141210]/12 bg-[#F7F1E7]/74 px-3 py-2 font-mono text-xs text-[#141210]/58">
              {dashboard ? `month ${dashboard.month}` : "loading"}
            </span>
            <button
              type="button"
              onClick={refreshDashboard}
              className="inline-flex items-center gap-2 border border-[#176B7D]/25 bg-[#F2EBDD] px-3 py-2 small-caps text-sm text-[#176B7D] transition hover:border-[#176B7D]"
            >
              <RefreshCw className="h-4 w-4" />
              refresh
            </button>
            <button
              type="button"
              onClick={onSignOut}
              className="border border-red-900/20 bg-red-50 px-3 py-2 small-caps text-sm text-red-600 transition hover:bg-red-100"
            >
              sign out
            </button>
          </div>
        </header>

        {dashboardQuery.error ? (
          <div className="border border-red-300 bg-red-50 px-4 py-3 font-mono text-sm text-red-700">
            {(dashboardQuery.error as Error).message}
          </div>
        ) : null}

        {dashboard ? (
          <>
            <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {organizationMetricItems(dashboard).map((item) => (
                <OwnerMetric key={item.label} {...item} />
              ))}
            </section>

            <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_24rem]">
              <OrganizationMembersTable
                dashboard={dashboard}
                currentUserId={user.id}
                canAssignOwner={user.site_role === "site_owner"}
                onRoleChange={handleRoleChange}
                onRemove={handleRemoveMember}
              />
              <OrganizationAdminPanel
                dashboard={dashboard}
                email={email}
                role={role}
                actionError={actionError}
                actionMessage={actionMessage}
                canAssignOwner={user.site_role === "site_owner"}
                onEmailChange={setEmail}
                onRoleChange={setRole}
                onAddMember={handleAddMember}
              />
            </section>

            <OrganizationEventsPanel dashboard={dashboard} />
          </>
        ) : (
          <div className="border border-[#141210]/12 bg-[#F7F1E7]/74 px-4 py-12 text-center font-mono text-sm text-[#141210]/56">
            Loading organization dashboard...
          </div>
        )}
      </main>
    </div>
  );
}

function OwnerDashboardPage({
  user,
  onBack,
  onOpenOrganizationAdmin,
  onSignOut,
}: {
  user: CurrentUser;
  onBack: () => void;
  onOpenOrganizationAdmin: (organizationId: string) => void;
  onSignOut: () => Promise<void>;
}) {
  const dashboardQuery = useQuery({
    queryKey: ["owner", "dashboard"],
    queryFn: getOwnerDashboard,
    enabled: user.site_role === "site_owner",
    refetchInterval: 30_000,
  });
  const dashboard = dashboardQuery.data;

  useEffect(() => {
    if (user.site_role === "site_owner") trackEvent("cerno_owner_dashboard_opened");
  }, [user.site_role]);

  if (user.site_role !== "site_owner") {
    return (
      <div className="flex min-h-full items-center justify-center bg-[#F2EBDD] px-6 text-[#141210]">
        <div className="max-w-md border border-red-900/20 bg-red-50 p-6">
          <div className="small-caps text-sm text-red-600">owner access required</div>
          <p className="mt-3 text-sm leading-6 text-red-700">
            This dashboard is only available to the configured site owner account.
          </p>
          <button
            type="button"
            onClick={onBack}
            className="small-caps mt-5 border border-red-900/20 bg-white px-4 py-2 text-sm text-red-700"
          >
            back to workspaces
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="cerno-paper-grain min-h-full bg-[#F2EBDD] px-5 py-6 text-[#141210] sm:px-8 lg:px-10">
      <main className="mx-auto flex max-w-[92rem] flex-col gap-6">
        <header className="flex flex-col gap-4 border-b border-[#141210]/12 pb-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={onBack}
              className="text-[#141210]/64 transition hover:text-[#176B7D]"
              title="Back to workspaces"
            >
              <CernoLockup markClassName="h-6 w-6" wordmarkClassName="text-base" />
            </button>
            <div>
              <div className="small-caps text-sm text-[#176B7D]">site owner</div>
              <h1 className="mt-1 font-serif text-4xl leading-none text-[#141210]">
                Usage and limits
              </h1>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="border border-[#141210]/12 bg-[#F7F1E7]/74 px-3 py-2 font-mono text-xs text-[#141210]/58">
              {dashboard ? `month ${dashboard.month}` : "loading"}
            </span>
            <button
              type="button"
              onClick={() => {
                trackEvent("cerno_owner_dashboard_refreshed");
                dashboardQuery.refetch();
              }}
              className="inline-flex items-center gap-2 border border-[#176B7D]/25 bg-[#F2EBDD] px-3 py-2 small-caps text-sm text-[#176B7D] transition hover:border-[#176B7D]"
            >
              <RefreshCw className="h-4 w-4" />
              refresh
            </button>
            <button
              type="button"
              onClick={onSignOut}
              className="border border-red-900/20 bg-red-50 px-3 py-2 small-caps text-sm text-red-600 transition hover:bg-red-100"
            >
              sign out
            </button>
          </div>
        </header>

        {dashboardQuery.error ? (
          <div className="border border-red-300 bg-red-50 px-4 py-3 font-mono text-sm text-red-700">
            {(dashboardQuery.error as Error).message}
          </div>
        ) : null}

        {dashboard ? (
          <>
            <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {ownerMetricItems(dashboard).map((item) => (
                <OwnerMetric key={item.label} {...item} />
              ))}
            </section>

            <section className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(24rem,0.9fr)]">
              <OwnerOrganizationsTable
                dashboard={dashboard}
                onOpenOrganizationAdmin={onOpenOrganizationAdmin}
              />
              <OwnerEventsPanel dashboard={dashboard} />
            </section>

            <OwnerUsersTable dashboard={dashboard} />
          </>
        ) : (
          <div className="border border-[#141210]/12 bg-[#F7F1E7]/74 px-4 py-12 text-center font-mono text-sm text-[#141210]/56">
            Loading owner dashboard...
          </div>
        )}
      </main>
    </div>
  );
}

function OwnerMetric({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string;
  value: string;
  detail: string;
  icon: LucideIcon;
}) {
  return (
    <div className="border border-[#141210]/12 bg-[#F7F1E7]/74 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="small-caps text-sm text-[#141210]/50">{label}</div>
        <Icon className="h-4 w-4 text-[#176B7D]" />
      </div>
      <div className="mt-4 font-mono text-3xl text-[#141210]">{value}</div>
      <div className="mt-2 truncate text-sm text-[#141210]/56">{detail}</div>
    </div>
  );
}

function OrganizationAdminPanel({
  dashboard,
  email,
  role,
  actionError,
  actionMessage,
  canAssignOwner,
  onEmailChange,
  onRoleChange,
  onAddMember,
}: {
  dashboard: OrganizationAdminDashboard;
  email: string;
  role: OrganizationMemberBody["role"];
  actionError: string | null;
  actionMessage: string | null;
  canAssignOwner: boolean;
  onEmailChange: (value: string) => void;
  onRoleChange: (value: OrganizationMemberBody["role"]) => void;
  onAddMember: () => void;
}) {
  return (
    <aside className="h-fit border border-[#141210]/12 bg-[#F7F1E7]/74">
      <div className="border-b border-[#141210]/12 px-4 py-3">
        <div className="small-caps text-sm text-[#176B7D]">members</div>
        <h2 className="mt-1 font-serif text-2xl text-[#141210]">Add user</h2>
      </div>
      <div className="grid gap-3 p-4">
        <label className="grid gap-2">
          <span className="small-caps text-xs text-[#141210]/52">email</span>
          <input
            type="email"
            value={email}
            onChange={(event) => onEmailChange(event.target.value)}
            className="border border-[#141210]/12 bg-[#F2EBDD] px-3 py-2 font-mono text-sm text-[#141210] focus:outline-none focus:ring-1 focus:ring-[#176B7D]"
            placeholder="person@company.com"
          />
        </label>
        <label className="grid gap-2">
          <span className="small-caps text-xs text-[#141210]/52">role</span>
          <select
            value={role}
            onChange={(event) => onRoleChange(event.target.value as OrganizationMemberBody["role"])}
            className="border border-[#141210]/12 bg-[#F2EBDD] px-3 py-2 font-mono text-sm text-[#141210] focus:outline-none focus:ring-1 focus:ring-[#176B7D]"
          >
            <option value="member">member</option>
            <option value="viewer">viewer</option>
            <option value="admin">admin</option>
            {canAssignOwner ? <option value="owner">owner</option> : null}
          </select>
        </label>
        <button
          type="button"
          onClick={onAddMember}
          className="small-caps bg-[#D17B2E] px-4 py-3 text-sm text-[#141210] transition hover:bg-[#E89A48]"
        >
          add user
        </button>
        {actionMessage ? (
          <div className="border border-[#176B7D]/20 bg-[#C9E3E2]/20 px-3 py-2 font-mono text-xs text-[#176B7D]">
            {actionMessage}
          </div>
        ) : null}
        {actionError ? (
          <div className="border border-red-300 bg-red-50 px-3 py-2 font-mono text-xs text-red-700">
            {actionError}
          </div>
        ) : null}
      </div>
      <div className="border-t border-[#141210]/12 p-4 text-xs leading-5 text-[#141210]/58">
        Seats {dashboard.totals.users} of {dashboard.entitlements.seat_limit}. New emails are recorded as invites until the user signs in and has site access approval.
      </div>
    </aside>
  );
}

function OrganizationMembersTable({
  dashboard,
  currentUserId,
  canAssignOwner,
  onRoleChange,
  onRemove,
}: {
  dashboard: OrganizationAdminDashboard;
  currentUserId: string;
  canAssignOwner: boolean;
  onRoleChange: (userId: string, role: OrganizationMemberBody["role"]) => void;
  onRemove: (user: OrganizationAdminDashboard["users"][number]) => void;
}) {
  return (
    <section className="min-w-0 border border-[#141210]/12 bg-[#F7F1E7]/74">
      <div className="flex items-end justify-between gap-4 border-b border-[#141210]/12 px-4 py-3">
        <div>
          <div className="small-caps text-sm text-[#176B7D]">users</div>
          <h2 className="mt-1 font-serif text-2xl text-[#141210]">Usage and access</h2>
        </div>
        <Users className="h-5 w-5 text-[#176B7D]" />
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[72rem] w-full border-collapse text-left text-sm">
          <thead className="small-caps border-b border-[#141210]/10 text-xs text-[#141210]/50">
            <tr>
              <th className="px-4 py-3 font-medium">user</th>
              <th className="px-4 py-3 font-medium">role</th>
              <th className="px-4 py-3 font-medium">sessions</th>
              <th className="px-4 py-3 font-medium">storage</th>
              <th className="px-4 py-3 font-medium">tokens</th>
              <th className="px-4 py-3 font-medium">uploads</th>
              <th className="px-4 py-3 font-medium">chat</th>
              <th className="px-4 py-3 font-medium">limits</th>
              <th className="px-4 py-3 font-medium">actions</th>
            </tr>
          </thead>
          <tbody>
            {dashboard.users.map((row) => (
              <tr key={row.user_id} className="border-b border-[#141210]/8 last:border-0">
                <td className="px-4 py-3">
                  <div className="font-mono text-sm text-[#141210]">{row.email}</div>
                  <div className="mt-1 text-xs text-[#141210]/48">
                    {row.name || "unnamed"} · {row.access_status} · seen {formatActivity(row.last_seen_at)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <select
                    value={row.membership.role}
                    onChange={(event) =>
                      onRoleChange(row.user_id, event.target.value as OrganizationMemberBody["role"])
                    }
                    className="border border-[#141210]/12 bg-[#F2EBDD] px-2 py-1 font-mono text-xs text-[#141210]"
                  >
                    <option value="member">member</option>
                    <option value="viewer">viewer</option>
                    <option value="admin">admin</option>
                    {canAssignOwner || row.membership.role === "owner" ? (
                      <option value="owner">owner</option>
                    ) : null}
                  </select>
                </td>
                <td className="px-4 py-3 font-mono">{row.session_count}</td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.storage_bytes)}</div>
                  <div className="text-xs text-[#141210]/48">
                    org cap {formatLimitBytes(row.effective_limits.organization_storage_quota_bytes)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatCompactNumber(row.llm_tokens_month)}</div>
                  <div className="text-xs text-[#141210]/48">
                    user cap {formatLimitNumber(row.effective_limits.user_monthly_token_limit)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.upload_bytes_month)}</div>
                  <div className="text-xs text-[#141210]/48">this month</div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{row.chat_turns_month} turns</div>
                  <div className="text-xs text-[#141210]/48">
                    avg {formatMs(row.avg_chat_response_ms)}
                  </div>
                </td>
                <td className="px-4 py-3 text-xs leading-5 text-[#141210]/62">
                  sessions {formatLimitNumber(row.effective_limits.user_max_sessions)}
                  <br />
                  file {formatLimitBytes(row.effective_limits.user_max_file_size_bytes)}
                  <br />
                  jobs {formatLimitNumber(row.effective_limits.user_max_concurrent_jobs)}
                </td>
                <td className="px-4 py-3">
                  <button
                    type="button"
                    onClick={() => onRemove(row)}
                    disabled={row.user_id === currentUserId}
                    className="small-caps border border-red-900/20 bg-red-50 px-3 py-2 text-xs text-red-600 transition hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function OrganizationEventsPanel({ dashboard }: { dashboard: OrganizationAdminDashboard }) {
  return (
    <section className="border border-[#141210]/12 bg-[#F7F1E7]/74">
      <div className="flex items-end justify-between gap-4 border-b border-[#141210]/12 px-4 py-3">
        <div>
          <div className="small-caps text-sm text-[#176B7D]">events</div>
          <h2 className="mt-1 font-serif text-2xl text-[#141210]">Recent org activity</h2>
        </div>
        <Activity className="h-5 w-5 text-[#176B7D]" />
      </div>
      <div className="grid divide-y divide-[#141210]/8">
        {dashboard.recent_events.length > 0 ? (
          dashboard.recent_events.slice(0, 18).map((event) => (
            <div
              key={`${event.occurred_at}:${event.event_name}:${event.user_id ?? ""}`}
              className="grid gap-1 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_10rem]"
            >
              <div className="min-w-0">
                <div className="font-mono text-sm text-[#141210]">{event.event_name}</div>
                <div className="truncate text-xs text-[#141210]/48">
                  {event.user_id ?? "system"} · {event.session_id ?? "no session"}
                </div>
              </div>
              <div className="text-xs text-[#141210]/48 sm:text-right">
                {formatActivity(event.occurred_at)}
              </div>
            </div>
          ))
        ) : (
          <div className="px-4 py-8 font-mono text-sm text-[#141210]/52">
            No product events recorded for this organization yet.
          </div>
        )}
      </div>
    </section>
  );
}

function OwnerOrganizationsTable({
  dashboard,
  onOpenOrganizationAdmin,
}: {
  dashboard: OwnerDashboard;
  onOpenOrganizationAdmin: (organizationId: string) => void;
}) {
  return (
    <section className="min-w-0 border border-[#141210]/12 bg-[#F7F1E7]/74">
      <div className="flex items-end justify-between gap-4 border-b border-[#141210]/12 px-4 py-3">
        <div>
          <div className="small-caps text-sm text-[#176B7D]">organizations</div>
          <h2 className="mt-1 font-serif text-2xl text-[#141210]">Org usage and limits</h2>
        </div>
        <Building2 className="h-5 w-5 text-[#176B7D]" />
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[58rem] w-full border-collapse text-left text-sm">
          <thead className="small-caps border-b border-[#141210]/10 text-xs text-[#141210]/50">
            <tr>
              <th className="px-4 py-3 font-medium">org</th>
              <th className="px-4 py-3 font-medium">users</th>
              <th className="px-4 py-3 font-medium">sessions</th>
              <th className="px-4 py-3 font-medium">storage</th>
              <th className="px-4 py-3 font-medium">tokens</th>
              <th className="px-4 py-3 font-medium">uploads</th>
              <th className="px-4 py-3 font-medium">jobs</th>
              <th className="px-4 py-3 font-medium">limits</th>
              <th className="px-4 py-3 font-medium">admin</th>
            </tr>
          </thead>
          <tbody>
            {dashboard.organizations.map((row) => (
              <tr key={row.organization.id} className="border-b border-[#141210]/8 last:border-0">
                <td className="px-4 py-3">
                  <div className="font-mono text-sm text-[#141210]">{row.organization.name}</div>
                  <div className="mt-1 text-xs text-[#141210]/48">
                    {row.entitlements.plan_name} · {row.entitlements.contract_status} · {formatActivity(row.last_activity_at ?? row.organization.updated_at)}
                  </div>
                </td>
                <td className="px-4 py-3 font-mono">{row.user_count}</td>
                <td className="px-4 py-3 font-mono">{row.session_count}</td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.storage_bytes)}</div>
                  <div className="text-xs text-[#141210]/48">
                    of {formatLimitBytes(row.entitlements.storage_quota_bytes)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatCompactNumber(row.llm_tokens_month)}</div>
                  <div className="text-xs text-[#141210]/48">
                    of {formatLimitNumber(row.entitlements.monthly_token_limit)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.upload_bytes_month)}</div>
                  <div className="text-xs text-[#141210]/48">{row.upload_count_month} files</div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{row.active_jobs} active</div>
                  <div className="text-xs text-[#141210]/48">{row.failed_jobs_month} failed</div>
                </td>
                <td className="px-4 py-3 text-xs leading-5 text-[#141210]/62">
                  seats {row.entitlements.seat_limit}
                  <br />
                  sessions {formatLimitNumber(row.entitlements.max_workspaces)}
                  <br />
                  jobs {formatLimitNumber(row.entitlements.max_concurrent_jobs)}
                </td>
                <td className="px-4 py-3">
                  <button
                    type="button"
                    onClick={() => onOpenOrganizationAdmin(row.organization.id)}
                    className="small-caps border border-[#176B7D]/25 bg-[#F2EBDD] px-3 py-2 text-xs text-[#176B7D] transition hover:border-[#176B7D]"
                  >
                    manage
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function OwnerUsersTable({ dashboard }: { dashboard: OwnerDashboard }) {
  const orgNames = new Map(
    dashboard.organizations.map((row) => [row.organization.id, row.organization.name]),
  );
  return (
    <section className="border border-[#141210]/12 bg-[#F7F1E7]/74">
      <div className="flex items-end justify-between gap-4 border-b border-[#141210]/12 px-4 py-3">
        <div>
          <div className="small-caps text-sm text-[#176B7D]">users</div>
          <h2 className="mt-1 font-serif text-2xl text-[#141210]">Per-user usage inside orgs</h2>
        </div>
        <Users className="h-5 w-5 text-[#176B7D]" />
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[74rem] w-full border-collapse text-left text-sm">
          <thead className="small-caps border-b border-[#141210]/10 text-xs text-[#141210]/50">
            <tr>
              <th className="px-4 py-3 font-medium">user</th>
              <th className="px-4 py-3 font-medium">org</th>
              <th className="px-4 py-3 font-medium">role</th>
              <th className="px-4 py-3 font-medium">sessions</th>
              <th className="px-4 py-3 font-medium">storage</th>
              <th className="px-4 py-3 font-medium">tokens</th>
              <th className="px-4 py-3 font-medium">uploads</th>
              <th className="px-4 py-3 font-medium">chat</th>
              <th className="px-4 py-3 font-medium">user limits</th>
            </tr>
          </thead>
          <tbody>
            {dashboard.users.map((row) => (
              <tr
                key={`${row.organization_id}:${row.user_id}`}
                className="border-b border-[#141210]/8 last:border-0"
              >
                <td className="px-4 py-3">
                  <div className="font-mono text-sm text-[#141210]">{row.email}</div>
                  <div className="mt-1 text-xs text-[#141210]/48">
                    {row.name || "unnamed"} · {row.access_status} · seen {formatActivity(row.last_seen_at)}
                  </div>
                </td>
                <td className="px-4 py-3">{orgNames.get(row.organization_id) ?? row.organization_id}</td>
                <td className="px-4 py-3">
                  <span className="border border-[#141210]/12 px-2 py-1 font-mono text-xs">
                    {row.membership.role}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{row.session_count}</div>
                  <div className="text-xs text-[#141210]/48">
                    cap {formatLimitNumber(row.effective_limits.user_max_sessions)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.storage_bytes)}</div>
                  <div className="text-xs text-[#141210]/48">
                    cap {formatLimitBytes(row.effective_limits.user_storage_quota_bytes)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatCompactNumber(row.llm_tokens_month)}</div>
                  <div className="text-xs text-[#141210]/48">
                    cap {formatLimitNumber(row.effective_limits.user_monthly_token_limit)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.upload_bytes_month)}</div>
                  <div className="text-xs text-[#141210]/48">
                    cap {formatLimitBytes(row.effective_limits.user_monthly_upload_bytes)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{row.chat_turns_month} turns</div>
                  <div className="text-xs text-[#141210]/48">
                    avg {formatMs(row.avg_chat_response_ms)}
                  </div>
                </td>
                <td className="px-4 py-3 text-xs leading-5 text-[#141210]/62">
                  daily tokens {formatLimitNumber(row.effective_limits.daily_token_limit)}
                  <br />
                  file {formatLimitBytes(row.effective_limits.user_max_file_size_bytes)}
                  <br />
                  jobs {formatLimitNumber(row.effective_limits.user_max_concurrent_jobs)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function OwnerEventsPanel({ dashboard }: { dashboard: OwnerDashboard }) {
  return (
    <section className="border border-[#141210]/12 bg-[#F7F1E7]/74">
      <div className="flex items-end justify-between gap-4 border-b border-[#141210]/12 px-4 py-3">
        <div>
          <div className="small-caps text-sm text-[#176B7D]">events</div>
          <h2 className="mt-1 font-serif text-2xl text-[#141210]">Recent product activity</h2>
        </div>
        <Activity className="h-5 w-5 text-[#176B7D]" />
      </div>
      <div className="max-h-[34rem] overflow-y-auto">
        {dashboard.recent_events.length > 0 ? (
          dashboard.recent_events.map((event) => (
            <div
              key={`${event.occurred_at}:${event.event_name}:${event.user_id ?? ""}`}
              className="grid gap-2 border-b border-[#141210]/8 px-4 py-3 last:border-0"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="font-mono text-sm text-[#141210]">{event.event_name}</div>
                <div className="text-xs text-[#141210]/48">{formatActivity(event.occurred_at)}</div>
              </div>
              <div className="truncate text-xs text-[#141210]/52">
                {event.user_id ?? "system"} · {event.session_id ?? "no session"}
              </div>
            </div>
          ))
        ) : (
          <div className="px-4 py-10 font-mono text-sm text-[#141210]/52">
            No product events recorded yet.
          </div>
        )}
      </div>
    </section>
  );
}

function organizationMetricItems(dashboard: OrganizationAdminDashboard) {
  return [
    {
      label: "users",
      value: `${dashboard.totals.users}/${dashboard.entitlements.seat_limit}`,
      detail: `${dashboard.current_role} access`,
      icon: Users,
    },
    {
      label: "llm tokens",
      value: formatCompactNumber(dashboard.totals.llm_tokens_month),
      detail: `of ${formatLimitNumber(dashboard.entitlements.monthly_token_limit)} this month`,
      icon: Activity,
    },
    {
      label: "storage",
      value: formatBytes(dashboard.totals.storage_bytes),
      detail: `of ${formatLimitBytes(dashboard.entitlements.storage_quota_bytes)}`,
      icon: HardDrive,
    },
    {
      label: "uploads",
      value: formatBytes(dashboard.totals.upload_bytes_month),
      detail: `${dashboard.totals.upload_count_month} files this month`,
      icon: FileSpreadsheet,
    },
    {
      label: "sessions",
      value: formatCompactNumber(dashboard.totals.sessions),
      detail: `cap ${formatLimitNumber(dashboard.entitlements.max_workspaces)}`,
      icon: Table2,
    },
    {
      label: "jobs",
      value: formatCompactNumber(dashboard.totals.active_jobs),
      detail: `${formatCompactNumber(dashboard.totals.failed_jobs_month)} failed this month`,
      icon: Database,
    },
    {
      label: "response time",
      value: formatMs(dashboard.totals.avg_chat_response_ms),
      detail: `${formatCompactNumber(dashboard.totals.chat_turns_month)} chat turns`,
      icon: Clock3,
    },
    {
      label: "processing",
      value: formatMs(dashboard.totals.avg_processing_ms),
      detail: "average job duration",
      icon: RefreshCw,
    },
  ] satisfies Array<{ label: string; value: string; detail: string; icon: LucideIcon }>;
}

function ownerMetricItems(dashboard: OwnerDashboard) {
  return [
    {
      label: "organizations",
      value: formatCompactNumber(dashboard.totals.organizations),
      detail: `${formatCompactNumber(dashboard.totals.users)} users`,
      icon: Building2,
    },
    {
      label: "llm tokens",
      value: formatCompactNumber(dashboard.totals.llm_tokens_month),
      detail: `${dashboard.month} across all orgs`,
      icon: Activity,
    },
    {
      label: "storage",
      value: formatBytes(dashboard.totals.storage_bytes),
      detail: `${formatBytes(dashboard.totals.upload_bytes_month)} uploaded this month`,
      icon: HardDrive,
    },
    {
      label: "response time",
      value: formatMs(dashboard.totals.avg_chat_response_ms),
      detail: `${formatCompactNumber(dashboard.totals.chat_turns_month)} chat turns`,
      icon: Clock3,
    },
    {
      label: "sessions",
      value: formatCompactNumber(dashboard.totals.sessions),
      detail: `${formatCompactNumber(dashboard.totals.upload_count_month)} uploads this month`,
      icon: Table2,
    },
    {
      label: "jobs",
      value: formatCompactNumber(dashboard.totals.active_jobs),
      detail: `${formatCompactNumber(dashboard.totals.failed_jobs_month)} failed this month`,
      icon: Database,
    },
    {
      label: "processing",
      value: formatMs(dashboard.totals.avg_processing_ms),
      detail: "average job duration",
      icon: RefreshCw,
    },
    {
      label: "errors",
      value: formatCompactNumber(dashboard.totals.errors_month),
      detail: "last 30 days",
      icon: AlertTriangle,
    },
  ] satisfies Array<{ label: string; value: string; detail: string; icon: LucideIcon }>;
}

function WorkspacesCartographyBackground({
  topoX,
  topoY,
}: {
  topoX: MotionValue<number>;
  topoY: MotionValue<number>;
}) {
  const lowerTopoX = useTransform(topoX, (value) => value * -0.55);
  const lowerTopoY = useTransform(topoY, (value) => value * -0.55);

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            "radial-gradient(circle at 74% 20%, rgba(31,134,154,0.10), transparent 28%), radial-gradient(circle at 82% 48%, rgba(209,123,46,0.12), transparent 32%), linear-gradient(180deg, rgba(255,255,255,0.30), transparent 42%)",
        }}
      />
      <motion.svg
        viewBox="0 0 1200 820"
        preserveAspectRatio="xMidYMid slice"
        className="absolute -right-[26%] top-[-22%] h-[92%] w-[82%] sm:-right-[20%] lg:-right-[10%] lg:h-[86%] lg:w-[66%]"
        aria-hidden="true"
        style={{ x: topoX, y: topoY }}
      >
        <g fill="none" strokeLinecap="round" strokeLinejoin="round">
          {topographicContours
            .filter((contour) => contour.level === 5)
            .map((contour) => (
              <path
                key={`workspace-fill-${contour.path}`}
                d={contour.path}
                fill="#D17B2E"
                opacity="0.055"
                stroke="none"
              />
            ))}
          {topographicContours.map((contour, idx) => {
            const highlighted = contour.level === 2 || contour.level === 8;
            const kaiLine = contour.level % 4 === 0;

            return (
              <motion.path
                key={`workspace-${contour.path}`}
                d={contour.path}
                stroke={highlighted ? "#D17B2E" : kaiLine ? "#176B7D" : "#141210"}
                strokeWidth={highlighted ? 2.2 : kaiLine ? 1.45 : 1.05}
                opacity={highlighted ? 0.34 : kaiLine ? 0.18 : 0.13}
                initial={{ pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={{ duration: 1.05, delay: idx * 0.025, ease: "easeOut" }}
              />
            );
          })}
        </g>
      </motion.svg>

      <motion.svg
        viewBox="0 0 620 420"
        preserveAspectRatio="xMidYMid slice"
        className="absolute bottom-[-15%] left-[-13%] h-[42%] w-[44%] opacity-60"
        aria-hidden="true"
        style={{ x: lowerTopoX, y: lowerTopoY }}
      >
        <g fill="none" strokeLinecap="round" strokeLinejoin="round">
          {topographicContours.slice(1, 12).map((contour, idx) => (
            <path
              key={`workspace-lower-${contour.path}`}
              d={contour.path}
              transform="translate(-550 -170) scale(0.92)"
              stroke={idx % 4 === 0 ? "#D17B2E" : "#176B7D"}
              strokeWidth={idx % 4 === 0 ? 2.1 : 1.1}
              opacity={idx % 4 === 0 ? 0.24 : 0.15}
            />
          ))}
        </g>
      </motion.svg>
    </div>
  );
}

function userDisplayName(user: CurrentUser): string {
  return user.name?.trim() || user.email.split("@")[0] || "Cerno user";
}

function userInitials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function WorkspaceStat({
  label,
  value,
}: {
  label: string;
  value: number | string;
}) {
  return (
    <div>
      <div className="small-caps text-sm text-[#141210]/46">{label}</div>
      <div className="mt-2 font-mono text-3xl text-[#141210]">{value}</div>
    </div>
  );
}

function WorkspaceLoadingPanel({ activeTab }: { activeTab: TabKey }) {
  const title =
    activeTab === "ask"
      ? "Loading chat"
      : "Loading insights";

  return (
    <div className="h-full bg-[#fffdf9] px-8 py-7">
      <div className="max-w-5xl">
        <div className="small-caps text-xs text-neutral-500">{title}</div>
        <div className="mt-4 grid gap-3">
          <SkeletonBlock className="h-12 w-2/3" />
          <SkeletonBlock className="h-24 w-full" />
          <SkeletonBlock className="h-24 w-5/6" />
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            <SkeletonBlock className="h-28" />
            <SkeletonBlock className="h-28" />
            <SkeletonBlock className="h-28" />
          </div>
        </div>
      </div>
    </div>
  );
}

function SkeletonBlock({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse border border-drift bg-drift/30 ${className}`}
      aria-hidden="true"
    />
  );
}

function WorkspaceRow({
  session,
  metric,
  onResume,
  onDelete,
  onPrefetch,
}: {
  session: Session;
  metric?: WorkspaceMetric;
  onResume: () => void;
  onDelete: () => void;
  onPrefetch: () => void;
}) {
  const activity = workspaceActivity(session, metric);
  const fileCount = metric?.loaded ? metric.fileCount : null;
  const rowCount = metric?.loaded ? metric.rowCount : null;

  return (
    <li>
      <motion.div
        layout
        className="group relative grid gap-4 overflow-hidden border border-[#141210]/12 bg-[#F7F1E7]/74 px-5 py-5 transition hover:border-[#176B7D]/40 hover:bg-[#F2EBDD]/88 md:grid-cols-[minmax(0,1fr)_auto]"
        onMouseEnter={onPrefetch}
        onFocus={onPrefetch}
        whileHover={{ y: -2 }}
        transition={{ duration: 0.22, ease: "easeOut" }}
      >
        <div className="pointer-events-none absolute inset-y-0 right-0 w-1/2 opacity-0 transition group-hover:opacity-100">
          <MiniTopoStrip />
        </div>
        <button
          type="button"
          onClick={onResume}
          className="relative z-10 min-w-0 text-left"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            <span className="h-3 w-3 bg-[#176B7D]" />
            <span className="truncate font-mono text-xl text-[#141210]">{session.name}</span>
            <span className="small-caps border border-[#141210]/12 bg-[#F2EBDD]/82 px-2 py-1 text-sm text-[#141210]/64">
              {workspaceStatusLabel(session)}
            </span>
          </div>
          <div className="mt-4 grid gap-4 font-mono text-sm text-[#141210]/68 sm:grid-cols-4">
            <WorkspaceFact label="files" value={formatMaybeNumber(fileCount)} />
            <WorkspaceFact label="rows" value={formatMaybeNumber(rowCount)} />
            <WorkspaceFact label="last activity" value={formatActivity(activity)} />
            <WorkspaceFact
              label="created"
              value={formatActivity(session.created_at)}
            />
          </div>
        </button>
        <div className="relative z-10 flex items-center gap-2 md:flex-col md:items-end md:justify-between">
          <button
            type="button"
            onClick={onResume}
            className="small-caps bg-[#141210] px-4 py-2 text-sm text-[#F2EBDD] transition hover:bg-[#176B7D]"
          >
            open
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="small-caps border border-red-900/20 bg-red-50 px-3 py-2 text-sm text-red-600 transition hover:border-red-900/40 hover:bg-red-100"
          >
            delete
          </button>
        </div>
      </motion.div>
    </li>
  );
}

function WorkspaceFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="small-caps text-sm text-[#141210]/36">{label}</div>
      <div className="mt-1 text-base text-[#141210]">{value}</div>
    </div>
  );
}

function buildWorkspaceStats(
  sessions: Session[],
  metrics: Record<string, WorkspaceMetric>,
) {
  const filesUploaded = sessions.reduce(
    (sum, session) => sum + (metrics[session.id]?.fileCount ?? 0),
    0,
  );
  const totalRows = sessions.reduce(
    (sum, session) => sum + (metrics[session.id]?.rowCount ?? 0),
    0,
  );
  const latest = sessions
    .map((session) => new Date(workspaceActivity(session, metrics[session.id])))
    .filter((date) => !Number.isNaN(date.getTime()))
    .sort((a, b) => b.getTime() - a.getTime())[0];
  return {
    workspaces: formatCompactNumber(sessions.length),
    filesUploaded: formatCompactNumber(filesUploaded),
    lastActivity: latest ? formatActivity(latest.toISOString()) : "none",
    totalRows: formatCompactNumber(totalRows),
  };
}

function workspaceActivity(session: Session, metric?: WorkspaceMetric): string {
  return metric?.lastOpenedAt ?? session.created_at;
}

function workspaceStatusLabel(session: Session): string {
  if (session.discovery_status === "pending_review") return "review";
  if (session.discovery_status === "approved") return "approved";
  return session.status;
}

function formatMaybeNumber(value: number | null): string {
  return value === null ? "loading" : formatCompactNumber(value);
}

function formatCompactNumber(value: number): string {
  if (value < 1_000) return value.toLocaleString();
  if (value < 1_000_000) {
    const compact = value / 1_000;
    return `${compact >= 10 ? compact.toFixed(0) : compact.toFixed(1)}K`;
  }
  const compact = value / 1_000_000;
  return `${compact >= 10 ? compact.toFixed(0) : compact.toFixed(1)}M`;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value.toLocaleString()} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = value / 1024;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}

function formatLimitNumber(value: number | null): string {
  return value === null ? "org default" : formatCompactNumber(value);
}

function formatLimitBytes(value: number | null): string {
  return value === null ? "org default" : formatBytes(value);
}

function formatMs(value: number | null): string {
  if (value === null) return "n/a";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} s`;
}

function formatActivity(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  const now = new Date();
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

function workspaceOpenedKey(id: string): string {
  return `cerno:workspace-opened:${id}`;
}

function markWorkspaceOpened(id: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(workspaceOpenedKey(id), new Date().toISOString());
}

function getWorkspaceLastOpened(id: string): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(workspaceOpenedKey(id));
}
