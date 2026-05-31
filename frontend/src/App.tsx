import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Building2,
  Clock3,
  Database,
  HardDrive,
  RefreshCw,
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
import {
  FilesPanel,
  WorkspaceSidebar,
} from "./components/SessionPanels";
import { SchemaTab } from "./components/SchemaTab";
import { SessionHeader } from "./components/SessionHeader";
import { Shell, type TabKey } from "./components/Shell";
import { AuthGate } from "./components/AuthGate";
import { BetaGate } from "./components/BetaGate";
import { LandingPage as CernoLanding } from "./components/landing/LandingPage";
import { ThemeProvider, ThemeToggle } from "./components/Theme";
import { ActivityChart, TopBarChart, ChartPanel } from "./components/DashboardCharts";
import { type CurrentUser } from "./lib/auth";
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
        <ThemeProvider defaultTheme="light"><AuthGate>
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
        </AuthGate></ThemeProvider>
      );
    }
    if (route.kind === "org-admin") {
      return (
        <ThemeProvider defaultTheme="light"><AuthGate>
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
        </AuthGate></ThemeProvider>
      );
    }
    return (
      <ThemeProvider defaultTheme="light"><AuthGate>
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
      </AuthGate></ThemeProvider>
    );
  }

  const chatReady = discoveryStatus === "approved";

  return (
    <ThemeProvider defaultTheme="light"><AuthGate>
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
    </AuthGate></ThemeProvider>
  );
}

function LandingPage({ onEnter }: { onEnter: () => void }) {
  return <CernoLanding onEnter={onEnter} />;
}

function AppSigil({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} fill="none" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" stroke="currentColor" strokeOpacity="0.35" />
      <circle cx="16" cy="16" r="9.5" stroke="currentColor" strokeOpacity="0.55" />
      <circle cx="16" cy="16" r="3.4" fill="currentColor" />
      <path d="M16 1.5v5M16 25.5v5M1.5 16h5M25.5 16h5" stroke="currentColor" strokeOpacity="0.5" />
    </svg>
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
  const canCreateWorkspaces = user.organizations.some(
    (org) => org.role === "admin" || org.role === "member",
  );

  useEffect(() => {
    sessions.slice(0, 6).forEach((session) => onPrefetch(session.id));
  }, [onPrefetch, sessions]);

  const handleStart = () => {
    if (!canCreateWorkspaces) return;
    const trimmed = name.trim();
    const fallback = `workspace ${new Date().toLocaleDateString()}`;
    onStart(trimmed.length > 0 ? trimmed : fallback);
  };

  const handleKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !starting && canCreateWorkspaces) handleStart();
  };

  const handleDelete = (s: Session) => {
    const ok = window.confirm(
      `Delete workspace "${s.name}"? Files, links, insights, and chat history will be removed. This cannot be undone.`,
    );
    if (ok) onDelete(s.id);
  };

  return (
    <div className="cerno-void relative min-h-full w-full overflow-hidden">
      <div className="pointer-events-none absolute inset-0 cerno-grid opacity-50" />
      <main className="relative z-10 mx-auto flex w-full max-w-[1400px] flex-col px-5 py-6 sm:px-8">
        <header className="flex items-center justify-between gap-4 border-b border-border pb-4">
          <button
            type="button"
            onClick={onBackToLanding}
            className="flex items-center gap-3 text-primary transition-opacity hover:opacity-80"
          >
            <AppSigil className="h-5 w-5" />
            <span className="font-display text-lg font-medium tracking-[0.2em] text-foreground">
              CERNO
            </span>
            <span className="hidden font-hud text-[9px] text-foreground/35 sm:inline">
              WORKSPACES
            </span>
          </button>
          <ThemeToggle />
        </header>

        <div className="mt-10 grid gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="min-w-0">
            <motion.div
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
            >
              <div className="flex items-center gap-3 font-hud text-[10px] text-primary">
                <span className="h-px w-8 bg-primary/60" />
                YOUR WORKSPACES
              </div>
              <h1 className="mt-5 max-w-3xl font-display text-4xl leading-[0.98] tracking-tight text-foreground sm:text-5xl">
                Open a workspace, or start with new files.
              </h1>
              <p className="mt-5 max-w-2xl text-base leading-7 text-foreground/55">
                Upload Excel or CSV files, review the generated schema, then ask
                questions with context Cerno can verify.
              </p>
            </motion.div>

            <div className="mt-10 grid grid-cols-2 gap-px border border-border bg-border sm:grid-cols-4">
              <WorkspaceStat label="WORKSPACES" value={stats.workspaces} />
              <WorkspaceStat label="FILES UPLOADED" value={stats.filesUploaded} />
              <WorkspaceStat label="LAST ACTIVITY" value={stats.lastActivity} />
              <WorkspaceStat label="TOTAL ROWS" value={stats.totalRows} />
            </div>

            <div className="mt-8 border border-border bg-card p-2">
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_13rem]">
                <label className="grid min-w-0 gap-1.5 border border-border bg-background px-4 py-3 text-left">
                  <span className="font-hud text-[10px] text-muted-foreground">
                    WORKSPACE NAME
                  </span>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    onKeyDown={handleKey}
                    disabled={!canCreateWorkspaces}
                    placeholder="Monthly sales, audit data…"
                    className="min-w-0 bg-transparent font-mono text-base text-foreground placeholder:text-muted-foreground/50 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                  />
                </label>
                <button
                  type="button"
                  onClick={handleStart}
                  disabled={starting || !canCreateWorkspaces}
                  className="bg-primary px-6 font-hud text-xs text-primary-foreground transition hover:opacity-95 disabled:opacity-40"
                >
                  {starting ? "STARTING…" : canCreateWorkspaces ? "START WORKSPACE" : "VIEW ONLY"}
                </button>
              </div>
            </div>

            {error ? (
              <div className="mt-4 flex w-full items-start justify-between gap-3 border border-destructive/40 bg-destructive/10 px-3 py-2 font-mono text-xs text-destructive">
                <span>{error}</span>
                <button type="button" onClick={onDismissError} className="hover:opacity-70">
                  {"×"}
                </button>
              </div>
            ) : null}

            <section className="mt-12">
              <div className="flex items-end justify-between gap-4 border-b border-border pb-4">
                <div className="flex items-center gap-3 font-hud text-[10px] text-primary">
                  <span className="text-foreground/30">{"//"}</span>
                  SAVED WORKSPACES
                </div>
                <span className="font-hud text-[10px] text-foreground/45">
                  {sessions.length} SAVED
                </span>
              </div>

              {sessions.length > 0 ? (
                <ul className="mt-5 grid gap-3">
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
                <div className="mt-5 border border-dashed border-border bg-card px-5 py-10 text-center font-mono text-sm text-muted-foreground">
                  No saved workspaces yet. Name one above to begin.
                </div>
              )}
            </section>
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
  const adminOrganizations = user.organizations.filter((org) => org.role === "admin");

  return (
    <aside className="h-fit border border-border bg-card p-4 lg:sticky lg:top-6">
      <div className="flex items-center gap-3 border-b border-border pb-4">
        {user.picture ? (
          <img
            src={user.picture}
            alt=""
            className="h-11 w-11 border border-border object-cover"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="flex h-11 w-11 items-center justify-center border border-border bg-background font-mono text-base text-primary">
            {userInitials(displayName)}
          </div>
        )}
        <div className="min-w-0">
          <div className="truncate font-mono text-sm text-foreground">{displayName}</div>
          <div className="truncate text-xs text-muted-foreground">{user.email}</div>
        </div>
      </div>

      <div className="grid gap-2 pt-4">
        {user.site_role === "site_owner" ? (
          <button
            type="button"
            onClick={onOpenOwnerDashboard}
            className="border border-primary/30 bg-background px-4 py-3 font-hud text-[11px] text-primary transition hover:bg-primary/10"
          >
            OWNER DASHBOARD
          </button>
        ) : null}
        {adminOrganizations.map((org) => (
          <button
            key={org.id}
            type="button"
            onClick={() => onOpenOrganizationAdmin(org.id)}
            className="border border-primary/30 bg-background px-4 py-3 font-hud text-[11px] text-primary transition hover:bg-primary/10"
          >
            {org.name.toUpperCase()} ADMIN
          </button>
        ))}
        <button
          type="button"
          onClick={onSignOut}
          className="border border-destructive/30 bg-background px-4 py-3 font-hud text-[11px] text-destructive transition hover:bg-destructive/10"
        >
          SIGN OUT
        </button>
      </div>
    </aside>
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
      <div className="cerno-void flex min-h-full items-center justify-center px-6">
        <div className="max-w-md border border-destructive/30 bg-card p-6">
          <div className="font-hud text-[10px] text-destructive">ORGANIZATION ADMIN REQUIRED</div>
          <p className="mt-3 text-sm leading-6 text-foreground/70">
            This dashboard is available to organization admins only.
          </p>
          <button
            type="button"
            onClick={onBack}
            className="mt-5 border border-border bg-background px-4 py-2 font-hud text-[11px] text-foreground/70 transition hover:text-primary"
          >
            BACK TO WORKSPACES
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="cerno-void min-h-full px-5 py-6 sm:px-8 lg:px-10">
      <main className="mx-auto flex max-w-[92rem] flex-col gap-6">
        <header className="flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-3 text-primary">
              <AppSigil className="h-5 w-5" />
              <span className="font-display text-lg font-medium tracking-[0.2em] text-foreground">
                CERNO
              </span>
            </span>
            <div className="border-l border-border pl-4">
              <div className="font-hud text-[10px] text-primary">ORGANIZATION ADMIN</div>
              <h1 className="mt-1 font-display text-3xl leading-none tracking-tight text-foreground">
                {dashboard?.organization.name ?? userOrg?.name ?? "Organization"}
              </h1>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ThemeToggle />
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-2 border border-border bg-card px-3 py-2 font-hud text-[11px] text-foreground/65 transition hover:text-primary"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              BACK
            </button>
            <span className="border border-border bg-card px-3 py-2 font-hud text-[10px] text-foreground/55">
              {dashboard ? `MONTH ${dashboard.month}` : "LOADING"}
            </span>
            <button
              type="button"
              onClick={refreshDashboard}
              className="inline-flex items-center gap-2 border border-primary/30 bg-background px-3 py-2 font-hud text-[11px] text-primary transition hover:bg-primary/10"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              REFRESH
            </button>
            <button
              type="button"
              onClick={onSignOut}
              className="border border-destructive/30 bg-background px-3 py-2 font-hud text-[11px] text-destructive transition hover:bg-destructive/10"
            >
              SIGN OUT
            </button>
          </div>
        </header>

        {dashboardQuery.error ? (
          <div className="border border-destructive/40 bg-destructive/10 px-4 py-3 font-mono text-sm text-destructive">
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

            <section className="grid gap-3 lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
              <ChartPanel title="ACTIVITY" subtitle="EVENTS / DAY">
                <ActivityChart events={dashboard.recent_events} />
              </ChartPanel>
              <ChartPanel title="TOP USERS" subtitle="TOKENS THIS MONTH">
                <TopBarChart
                  useSignal
                  items={dashboard.users.map((u) => ({
                    name: u.name || u.email.split("@")[0] || u.email,
                    value: u.llm_tokens_month,
                  }))}
                />
              </ChartPanel>
            </section>

            <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
              <OrganizationMembersTable
                dashboard={dashboard}
                currentUserId={user.id}
                onRoleChange={handleRoleChange}
                onRemove={handleRemoveMember}
              />
              <OrganizationAdminPanel
                dashboard={dashboard}
                email={email}
                role={role}
                actionError={actionError}
                actionMessage={actionMessage}
                onEmailChange={setEmail}
                onRoleChange={setRole}
                onAddMember={handleAddMember}
              />
            </section>
          </>
        ) : (
          <div className="border border-border bg-card px-4 py-12 text-center font-mono text-sm text-muted-foreground">
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
      <div className="cerno-void flex min-h-full items-center justify-center px-6">
        <div className="max-w-md border border-destructive/30 bg-card p-6">
          <div className="font-hud text-[10px] text-destructive">OWNER ACCESS REQUIRED</div>
          <p className="mt-3 text-sm leading-6 text-foreground/70">
            This dashboard is only available to the configured site owner account.
          </p>
          <button
            type="button"
            onClick={onBack}
            className="mt-5 border border-border bg-background px-4 py-2 font-hud text-[11px] text-foreground/70 transition hover:text-primary"
          >
            BACK TO WORKSPACES
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="cerno-void min-h-full px-5 py-6 sm:px-8 lg:px-10">
      <main className="mx-auto flex max-w-[92rem] flex-col gap-6">
        <header className="flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={onBack}
              className="flex items-center gap-3 text-primary transition-opacity hover:opacity-80"
              title="Back to workspaces"
            >
              <AppSigil className="h-5 w-5" />
              <span className="font-display text-lg font-medium tracking-[0.2em] text-foreground">
                CERNO
              </span>
            </button>
            <div className="border-l border-border pl-4">
              <div className="font-hud text-[10px] text-primary">SITE OWNER</div>
              <h1 className="mt-1 font-display text-3xl leading-none tracking-tight text-foreground">
                Usage and limits
              </h1>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ThemeToggle />
            <span className="border border-border bg-card px-3 py-2 font-hud text-[10px] text-foreground/55">
              {dashboard ? `MONTH ${dashboard.month}` : "LOADING"}
            </span>
            <button
              type="button"
              onClick={() => {
                trackEvent("cerno_owner_dashboard_refreshed");
                dashboardQuery.refetch();
              }}
              className="inline-flex items-center gap-2 border border-primary/30 bg-background px-3 py-2 font-hud text-[11px] text-primary transition hover:bg-primary/10"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              REFRESH
            </button>
            <button
              type="button"
              onClick={onSignOut}
              className="border border-destructive/30 bg-background px-3 py-2 font-hud text-[11px] text-destructive transition hover:bg-destructive/10"
            >
              SIGN OUT
            </button>
          </div>
        </header>

        {dashboardQuery.error ? (
          <div className="border border-destructive/40 bg-destructive/10 px-4 py-3 font-mono text-sm text-destructive">
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

            <section className="grid gap-3 lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
              <ChartPanel title="ACTIVITY" subtitle="PRODUCT EVENTS / DAY">
                <ActivityChart events={dashboard.recent_events} />
              </ChartPanel>
              <ChartPanel title="TOP ORGANIZATIONS" subtitle="TOKENS THIS MONTH">
                <TopBarChart
                  useSignal
                  items={dashboard.organizations.map((o) => ({
                    name: o.organization.name,
                    value: o.llm_tokens_month,
                  }))}
                />
              </ChartPanel>
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
          <div className="border border-border bg-card px-4 py-12 text-center font-mono text-sm text-muted-foreground">
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
    <div className="border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="font-hud text-[10px] text-foreground/50">{label}</div>
        <Icon className="h-4 w-4 text-primary" />
      </div>
      <div className="mt-4 font-display text-3xl text-foreground">{value}</div>
      <div className="mt-2 truncate text-xs text-foreground/55">{detail}</div>
    </div>
  );
}

function OrganizationAdminPanel({
  dashboard,
  email,
  role,
  actionError,
  actionMessage,
  onEmailChange,
  onRoleChange,
  onAddMember,
}: {
  dashboard: OrganizationAdminDashboard;
  email: string;
  role: OrganizationMemberBody["role"];
  actionError: string | null;
  actionMessage: string | null;
  onEmailChange: (value: string) => void;
  onRoleChange: (value: OrganizationMemberBody["role"]) => void;
  onAddMember: () => void;
}) {
  return (
    <aside className="h-fit border border-border bg-card">
      <div className="border-b border-border px-4 py-3">
        <div className="small-caps text-sm text-primary">members</div>
        <h2 className="mt-1 font-display text-2xl text-foreground">Add user</h2>
      </div>
      <div className="grid gap-3 p-4">
        <label className="grid gap-2">
          <span className="small-caps text-xs text-foreground/52">email</span>
          <input
            type="email"
            value={email}
            onChange={(event) => onEmailChange(event.target.value)}
            className="border border-border bg-background px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            placeholder="person@company.com"
          />
        </label>
        <label className="grid gap-2">
          <span className="small-caps text-xs text-foreground/52">role</span>
          <select
            value={role}
            onChange={(event) => onRoleChange(event.target.value as OrganizationMemberBody["role"])}
            className="border border-border bg-background px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="admin">admin</option>
            <option value="member">member</option>
            <option value="viewer">viewer</option>
          </select>
        </label>
        <button
          type="button"
          onClick={onAddMember}
          className="bg-primary px-4 py-3 font-hud text-[11px] text-primary-foreground transition hover:opacity-95"
        >
          add user
        </button>
        {actionMessage ? (
          <div className="border border-primary/30 bg-primary/10 px-3 py-2 font-mono text-xs text-primary">
            {actionMessage}
          </div>
        ) : null}
        {actionError ? (
          <div className="border border-destructive/40 bg-destructive/10 px-3 py-2 font-mono text-xs text-destructive">
            {actionError}
          </div>
        ) : null}
      </div>
      <div className="border-t border-border p-4 text-xs leading-5 text-foreground/58">
        Seats {dashboard.totals.users} of {dashboard.entitlements.seat_limit}. New emails are recorded as invites until the user signs in and has site access approval.
      </div>
    </aside>
  );
}

function OrganizationMembersTable({
  dashboard,
  currentUserId,
  onRoleChange,
  onRemove,
}: {
  dashboard: OrganizationAdminDashboard;
  currentUserId: string;
  onRoleChange: (userId: string, role: OrganizationMemberBody["role"]) => void;
  onRemove: (user: OrganizationAdminDashboard["users"][number]) => void;
}) {
  const isSingleUserOrg = dashboard.users.length === 1;

  return (
    <section className="min-w-0 border border-border bg-card">
      <div className="flex items-end justify-between gap-4 border-b border-border px-4 py-3">
        <div>
          <div className="small-caps text-sm text-primary">users</div>
          <h2 className="mt-1 font-display text-2xl text-foreground">Usage and access</h2>
        </div>
        <Users className="h-5 w-5 text-primary" />
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[56rem] w-full border-collapse text-left text-sm">
          <thead className="small-caps border-b border-border text-xs text-foreground/50">
            <tr>
              <th className="px-4 py-3 font-medium">user</th>
              <th className="px-4 py-3 font-medium">role</th>
              <th className="px-4 py-3 font-medium">LLM</th>
              <th className="px-4 py-3 font-medium">storage</th>
              <th className="px-4 py-3 font-medium">sessions</th>
              <th className="px-4 py-3 font-medium">last active</th>
              <th className="px-4 py-3 font-medium">actions</th>
            </tr>
          </thead>
          <tbody>
            {dashboard.users.map((row) => (
              <tr key={row.user_id} className="border-b border-border last:border-0">
                <td className="px-4 py-3">
                  <div className="font-mono text-sm text-foreground">{row.email}</div>
                  <div className="mt-1 text-xs text-foreground/48">
                    {row.name || "unnamed"} · {row.access_status}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <select
                    value={row.membership.role}
                    disabled={isSingleUserOrg}
                    onChange={(event) =>
                      onRoleChange(row.user_id, event.target.value as OrganizationMemberBody["role"])
                    }
                    className="border border-border bg-background px-2 py-1 font-mono text-xs text-foreground disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <option value="admin">admin</option>
                    <option value="member">member</option>
                    <option value="viewer">viewer</option>
                  </select>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatPercent(row.llm_tokens_month, dashboard.entitlements.monthly_token_limit)}</div>
                  <div className="text-xs text-foreground/48">of org limit</div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.storage_bytes)}</div>
                  <div className="text-xs text-foreground/48">
                    {formatPercent(row.storage_bytes, dashboard.entitlements.storage_quota_bytes)} of org storage
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{row.session_count}</div>
                  <div className="text-xs text-foreground/48">
                    {formatPercent(row.session_count, dashboard.totals.sessions)} of org sessions
                  </div>
                </td>
                <td className="px-4 py-3 font-mono text-xs text-foreground/62">
                  {formatActivity(row.last_seen_at)}
                </td>
                <td className="px-4 py-3">
                  <button
                    type="button"
                    onClick={() => onRemove(row)}
                    disabled={row.user_id === currentUserId || isSingleUserOrg}
                    className="small-caps border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive transition hover:bg-destructive/20 disabled:cursor-not-allowed disabled:opacity-40"
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

function OwnerOrganizationsTable({
  dashboard,
  onOpenOrganizationAdmin,
}: {
  dashboard: OwnerDashboard;
  onOpenOrganizationAdmin: (organizationId: string) => void;
}) {
  return (
    <section className="min-w-0 border border-border bg-card">
      <div className="flex items-end justify-between gap-4 border-b border-border px-4 py-3">
        <div>
          <div className="small-caps text-sm text-primary">organizations</div>
          <h2 className="mt-1 font-display text-2xl text-foreground">Org usage and limits</h2>
        </div>
        <Building2 className="h-5 w-5 text-primary" />
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[58rem] w-full border-collapse text-left text-sm">
          <thead className="small-caps border-b border-border text-xs text-foreground/50">
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
              <tr key={row.organization.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3">
                  <div className="font-mono text-sm text-foreground">{row.organization.name}</div>
                  <div className="mt-1 text-xs text-foreground/48">
                    {row.entitlements.plan_name} · {row.entitlements.contract_status} · {formatActivity(row.last_activity_at ?? row.organization.updated_at)}
                  </div>
                </td>
                <td className="px-4 py-3 font-mono">{row.user_count}</td>
                <td className="px-4 py-3 font-mono">{row.session_count}</td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.storage_bytes)}</div>
                  <div className="text-xs text-foreground/48">
                    of {formatLimitBytes(row.entitlements.storage_quota_bytes)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatCompactNumber(row.llm_tokens_month)}</div>
                  <div className="text-xs text-foreground/48">
                    of {formatLimitNumber(row.entitlements.monthly_token_limit)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.upload_bytes_month)}</div>
                  <div className="text-xs text-foreground/48">{row.upload_count_month} files</div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{row.active_jobs} active</div>
                  <div className="text-xs text-foreground/48">{row.failed_jobs_month} failed</div>
                </td>
                <td className="px-4 py-3 text-xs leading-5 text-foreground/62">
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
                    className="small-caps border border-primary/30 bg-background px-3 py-2 text-xs text-primary transition hover:border-primary"
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
    <section className="border border-border bg-card">
      <div className="flex items-end justify-between gap-4 border-b border-border px-4 py-3">
        <div>
          <div className="small-caps text-sm text-primary">users</div>
          <h2 className="mt-1 font-display text-2xl text-foreground">Per-user usage inside orgs</h2>
        </div>
        <Users className="h-5 w-5 text-primary" />
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[74rem] w-full border-collapse text-left text-sm">
          <thead className="small-caps border-b border-border text-xs text-foreground/50">
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
                className="border-b border-border last:border-0"
              >
                <td className="px-4 py-3">
                  <div className="font-mono text-sm text-foreground">{row.email}</div>
                  <div className="mt-1 text-xs text-foreground/48">
                    {row.name || "unnamed"} · {row.access_status} · seen {formatActivity(row.last_seen_at)}
                  </div>
                </td>
                <td className="px-4 py-3">{orgNames.get(row.organization_id) ?? row.organization_id}</td>
                <td className="px-4 py-3">
                  <span className="border border-border px-2 py-1 font-mono text-xs">
                    {row.membership.role}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{row.session_count}</div>
                  <div className="text-xs text-foreground/48">
                    cap {formatLimitNumber(row.effective_limits.user_max_sessions)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.storage_bytes)}</div>
                  <div className="text-xs text-foreground/48">
                    cap {formatLimitBytes(row.effective_limits.user_storage_quota_bytes)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatCompactNumber(row.llm_tokens_month)}</div>
                  <div className="text-xs text-foreground/48">
                    cap {formatLimitNumber(row.effective_limits.user_monthly_token_limit)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{formatBytes(row.upload_bytes_month)}</div>
                  <div className="text-xs text-foreground/48">
                    cap {formatLimitBytes(row.effective_limits.user_monthly_upload_bytes)}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="font-mono">{row.chat_turns_month} turns</div>
                  <div className="text-xs text-foreground/48">
                    avg {formatMs(row.avg_chat_response_ms)}
                  </div>
                </td>
                <td className="px-4 py-3 text-xs leading-5 text-foreground/62">
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
    <section className="border border-border bg-card">
      <div className="flex items-end justify-between gap-4 border-b border-border px-4 py-3">
        <div>
          <div className="small-caps text-sm text-primary">events</div>
          <h2 className="mt-1 font-display text-2xl text-foreground">Recent product activity</h2>
        </div>
        <Activity className="h-5 w-5 text-primary" />
      </div>
      <div className="max-h-[34rem] overflow-y-auto">
        {dashboard.recent_events.length > 0 ? (
          dashboard.recent_events.map((event) => (
            <div
              key={`${event.occurred_at}:${event.event_name}:${event.user_id ?? ""}`}
              className="grid gap-2 border-b border-border px-4 py-3 last:border-0"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="font-mono text-sm text-foreground">{event.event_name}</div>
                <div className="text-xs text-foreground/48">{formatActivity(event.occurred_at)}</div>
              </div>
              <div className="truncate text-xs text-foreground/52">
                {event.user_id ?? "system"} · {event.session_id ?? "no session"}
              </div>
            </div>
          ))
        ) : (
          <div className="px-4 py-10 font-mono text-sm text-foreground/52">
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
      detail: `${dashboard.current_role} dashboard access`,
      icon: Users,
    },
    {
      label: "LLM monthly usage",
      value: formatPercent(dashboard.totals.llm_tokens_month, dashboard.entitlements.monthly_token_limit),
      detail: "monthly limit used",
      icon: Activity,
    },
    {
      label: "storage",
      value: `${formatBytes(dashboard.totals.storage_bytes)}`,
      detail: `of ${formatLimitBytes(dashboard.entitlements.storage_quota_bytes)}`,
      icon: HardDrive,
    },
    {
      label: "sessions",
      value: formatCompactNumber(dashboard.totals.sessions),
      detail: `cap ${formatLimitNumber(dashboard.entitlements.max_workspaces)}`,
      icon: Table2,
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

function WorkspaceStat({
  label,
  value,
}: {
  label: string;
  value: number | string;
}) {
  return (
    <div className="bg-card px-4 py-4">
      <div className="font-hud text-[9px] text-foreground/40">{label}</div>
      <div className="mt-1.5 font-display text-2xl text-foreground">{value}</div>
    </div>
  );
}

function WorkspaceLoadingPanel({ activeTab }: { activeTab: TabKey }) {
  const title = activeTab === "ask" ? "LOADING CHAT" : "LOADING INSIGHTS";

  return (
    <div className="h-full bg-background px-8 py-7">
      <div className="max-w-5xl">
        <div className="font-hud text-[10px] text-muted-foreground">{title}</div>
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
      className={`animate-pulse border border-border bg-muted ${className}`}
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
      <div
        className="group relative grid gap-4 overflow-hidden border border-border bg-card px-5 py-5 transition-colors hover:border-primary/40 hover:bg-primary/[0.03] md:grid-cols-[minmax(0,1fr)_auto]"
        onMouseEnter={onPrefetch}
        onFocus={onPrefetch}
      >
        <button
          type="button"
          onClick={onResume}
          className="relative z-10 min-w-0 text-left"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            <span className="h-2.5 w-2.5 bg-primary" />
            <span className="truncate font-display text-xl text-foreground">{session.name}</span>
            <span className="border border-border bg-background px-2 py-0.5 font-hud text-[9px] text-muted-foreground">
              {workspaceStatusLabel(session).toUpperCase()}
            </span>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-4">
            <WorkspaceFact label="FILES" value={formatMaybeNumber(fileCount)} />
            <WorkspaceFact label="ROWS" value={formatMaybeNumber(rowCount)} />
            <WorkspaceFact label="LAST ACTIVITY" value={formatActivity(activity)} />
            <WorkspaceFact label="CREATED" value={formatActivity(session.created_at)} />
          </div>
        </button>
        <div className="relative z-10 flex items-center gap-2 md:flex-col md:items-end md:justify-between">
          <button
            type="button"
            onClick={onResume}
            className="bg-primary px-4 py-2 font-hud text-[11px] text-primary-foreground transition hover:opacity-95"
          >
            OPEN
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="border border-destructive/30 px-3 py-2 font-hud text-[11px] text-destructive transition hover:bg-destructive/10"
          >
            DELETE
          </button>
        </div>
      </div>
    </li>
  );
}

function WorkspaceFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="font-hud text-[9px] text-foreground/35">{label}</div>
      <div className="mt-1 font-mono text-sm text-foreground">{value}</div>
    </div>
  );
}

function userDisplayName(user: CurrentUser): string {
  return user.name?.trim() || user.email.split("@")[0] || user.email;
}

function userInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
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

function formatPercent(value: number, total: number | null): string {
  if (!total || total <= 0) return "n/a";
  const percent = (value / total) * 100;
  if (percent > 0 && percent < 1) return "<1%";
  return `${Math.min(999, Math.round(percent)).toLocaleString()}%`;
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
