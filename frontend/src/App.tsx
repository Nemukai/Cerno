import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { ArrowRight, Database, LineChart, FileSpreadsheet, Sparkles, MessageSquare } from "lucide-react";
import {
  approveSchema,
  createSession,
  deleteFile,
  deleteSession,
  deleteTurn,
  getChatFeed,
  getDiscovery,
  getProcessingEvents,
  getSchemaGuide,
  getWorkspace,
  listFiles,
  listLinks,
  listSessions,
  processSession,
  streamChat,
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
  | { kind: "sessions" }
  | { kind: "session"; sessionId: string; tab: TabKey };

const SESSION_TABS: TabKey[] = ["ask", "insights", "files"];

function isTabKey(value: string | undefined): value is TabKey {
  return SESSION_TABS.includes(value as TabKey);
}

function readRoute(): AppRoute {
  const parts = window.location.pathname.split("/").filter(Boolean);
  if (parts.length === 0) return { kind: "landing" };
  if (parts[0] === "sessions") {
    if (parts.length === 1) return { kind: "sessions" };
    const sessionId = parts[1];
    if (!sessionId) return { kind: "sessions" };
    return {
      kind: "session",
      sessionId: decodeURIComponent(sessionId),
      tab: isTabKey(parts[2]) ? parts[2] : "ask",
    };
  }
  return { kind: "landing" };
}

function routePath(route: AppRoute): string {
  if (route.kind === "landing") return "/";
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
        markWorkspaceOpened(s.id);
        clearSessionState();
        setHomeView("sessions");
        setSession(s);
        setActiveTab("files");
        navigateSessionTab(s.id, "files");
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
      setActiveTab("files");
      navigateSessionTab(session.id, "files");
      try {
        await uploadFiles(session.id, deduped);
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
      setActiveTab("files");
      navigateSessionTab(session.id, "files");
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
    setActiveTab("insights");
    navigateSessionTab(session.id, "insights");
    try {
      const result = await processSession(session.id);
      setEvents(result.events);
      await Promise.all([
        refreshFiles(session.id),
        refreshDiscovery(session.id),
        refreshEvents(session.id),
        refreshSessions(),
      ]);
      await queryClient.invalidateQueries({ queryKey: ["workspace", session.id] });
    } catch (err) {
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
      } catch (err) {
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
              setLiveChat((current) =>
                current ? { ...current, turnId: event.turn_id, error: event.message } : current,
              );
              setError(event.message);
            }
          },
        );
        await refreshChatFeed(session.id);
        await queryClient.invalidateQueries({ queryKey: ["workspace", session.id] });
        setLiveChat(null);
      } catch (err) {
        setError((err as Error).message);
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
              onSignOut={async () => {
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
            onOpenFiles={() => handleTabChange("files")}
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
          />
        ) : null}
        {!workspaceLoading && files.length > 0 && activeTab === "files" ? (
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

function DotPattern() {
  return (
    <div className="absolute inset-0 z-0 opacity-20 pointer-events-none" style={{ backgroundImage: 'radial-gradient(#345A67 1px, transparent 1px)', backgroundSize: '32px 32px' }} />
  );
}

const landingWorkflow = [
  {
    index: "01",
    title: "Drop the files",
    body: "Excel and CSV uploads stay as source material while Cerno profiles every sheet.",
    icon: FileSpreadsheet,
  },
  {
    index: "02",
    title: "Approve the map",
    body: "Headers, meanings, relationships, caveats, and glossary terms become a reviewed guide.",
    icon: Database,
  },
  {
    index: "03",
    title: "Ask and shape",
    body: "Chat answers with tables, charts, and saved analysis pages grounded in that guide.",
    icon: MessageSquare,
  },
];

function LandingPage({
  onEnter,
}: {
  onEnter: () => void;
}) {
  const { user, loading } = useUser();
  
  useEffect(() => {
    if (!loading && user && user.access_status === "granted") {
      onEnter();
    }
  }, [user, loading, onEnter]);

  if (loading || (user && user.access_status === "granted")) {
    return <div className="flex h-screen items-center justify-center bg-tidepaper text-night-watch">Loading…</div>;
  }

  return (
    <div className="relative min-h-full overflow-hidden bg-tidepaper text-night-watch">
      <DotPattern />
      <main className="relative z-10 flex min-h-full flex-col">
        <header className="flex items-center justify-between border-b border-drift bg-tidepaper/85 px-6 py-5 backdrop-blur-md sm:px-10">
          <CernoLockup markClassName="h-6 w-6 text-deep-sea" wordmarkClassName="text-base text-night-watch" />
          <a
            href={googleLoginUrl()}
            className="small-caps border border-drift bg-tidepaper px-3 py-1.5 text-xs text-night-watch transition hover:bg-drift"
          >
            login
          </a>
        </header>

        <section className="relative z-10 flex min-h-[calc(100svh-4.6rem)] items-center px-6 py-12 sm:px-10 lg:py-14">
          <div className="mx-auto grid w-full max-w-7xl items-center gap-12 lg:grid-cols-[minmax(0,0.9fr)_minmax(30rem,1.1fr)]">
            <motion.div 
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, ease: "easeOut" }}
              className="border-l border-drift pl-6 sm:pl-8"
            >
              <motion.div 
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.3 }}
                className="flex items-center gap-2 small-caps text-xs text-deep-sea"
              >
                <Sparkles className="h-3 w-3" /> private beta for messy business data
              </motion.div>
              <h1 className="mt-5 max-w-3xl font-serif text-5xl leading-[1.02] text-night-watch sm:text-7xl lg:text-7xl">
                Research notebook meets observatory.
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-8 text-night-watch/70">
                Cerno turns office spreadsheets into a reviewed data map, then gives
                you a chat workspace that can explain fields, build dashboards, and
                keep the analysis grounded.
              </p>
              <div className="mt-10 flex flex-wrap items-center gap-4">
                <a
                  href={googleLoginUrl()}
                  className="group flex items-center gap-2 small-caps bg-night-watch px-6 py-3.5 text-sm text-tidepaper transition hover:bg-deep-sea"
                >
                  sign in to continue
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                </a>
                <span className="small-caps text-xs text-night-watch/50">
                  invite-only email access
                </span>
              </div>
              <div className="mt-12 grid max-w-2xl grid-cols-3 border-y border-drift">
                <LandingMeasure value="raw" label="files preserved" />
                <LandingMeasure value="map" label="approved context" />
                <LandingMeasure value="chat" label="visual analysis" />
              </div>
            </motion.div>

            <motion.div 
              initial={{ opacity: 0, x: 40 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 1, delay: 0.2, ease: "easeOut" }}
              className="relative min-h-[420px] w-full lg:min-h-[560px]"
            >
              <ObservatoryConsole />
            </motion.div>
          </div>
        </section>

        <section className="relative z-10 border-y border-drift bg-[#fbfaf6] px-6 py-10 sm:px-10">
          <div className="mx-auto grid max-w-7xl gap-8 lg:grid-cols-[20rem_minmax(0,1fr)]">
            <div>
              <div className="small-caps text-xs text-deep-sea">how it behaves</div>
              <h2 className="mt-3 font-serif text-4xl leading-tight text-night-watch">
                Not a spreadsheet viewer. A working room for the data.
              </h2>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              {landingWorkflow.map((item, idx) => (
                <LandingFact key={item.index} {...item} delay={idx * 0.08} />
              ))}
            </div>
          </div>
        </section>

        <section className="relative z-10 px-6 py-14 sm:px-10">
          <div className="mx-auto grid max-w-7xl gap-10 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <div>
              <div className="small-caps text-xs text-deep-sea">what Cerno keeps track of</div>
              <h2 className="mt-3 max-w-xl font-serif text-4xl leading-tight text-night-watch">
                The boring parts become durable context.
              </h2>
              <p className="mt-5 max-w-xl text-base leading-7 text-night-watch/65">
                Cerno remembers the schema guide, relationships, caveats, glossary
                terms, and generated views so each question starts from the same
                approved understanding.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <LandingDetail label="headers" value="found before analysis" />
              <LandingDetail label="relationships" value="reviewed across files" />
              <LandingDetail label="dashboards" value="generated and editable" />
              <LandingDetail label="chat" value="grounded in approved docs" />
            </div>
          </div>
        </section>

        <section className="relative z-10 border-t border-drift bg-night-watch px-6 py-10 text-tidepaper sm:px-10">
          <div className="mx-auto flex max-w-7xl flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="small-caps text-xs text-clay">private beta</div>
              <h2 className="mt-2 font-serif text-3xl text-tidepaper">
                Bring the messy workbook. Cerno will build the first map.
              </h2>
            </div>
            <a
              href={googleLoginUrl()}
              className="group flex w-fit items-center gap-2 small-caps border border-tidepaper/30 px-5 py-3 text-sm text-tidepaper transition hover:border-clay hover:text-clay"
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

function LandingMeasure({ value, label }: { value: string; label: string }) {
  return (
    <div className="border-r border-drift py-4 pr-3 last:border-r-0 last:pl-4 sm:px-4 sm:first:pl-0">
      <div className="font-mono text-xl text-night-watch">{value}</div>
      <div className="small-caps mt-1 text-[11px] text-night-watch/45">{label}</div>
    </div>
  );
}

function ObservatoryConsole() {
  return (
    <div className="absolute inset-0 border border-drift bg-tidepaper shadow-[0_24px_80px_rgba(44,51,56,0.12)]">
      <div className="flex h-10 items-center justify-between border-b border-drift bg-[#fbfaf6] px-4">
        <div className="small-caps text-xs text-night-watch/50">live workspace model</div>
        <div className="flex gap-1.5">
          <span className="h-2 w-2 bg-drift" />
          <span className="h-2 w-2 bg-clay" />
          <span className="h-2 w-2 bg-deep-sea" />
        </div>
      </div>
      <div className="relative h-[calc(100%-2.5rem)] overflow-hidden">
        <div
          className="absolute inset-0 opacity-60"
          style={{
            backgroundImage:
              "linear-gradient(#E2DDD1 1px, transparent 1px), linear-gradient(90deg, #E2DDD1 1px, transparent 1px)",
            backgroundSize: "52px 52px",
          }}
        />
        <motion.div
          className="absolute inset-x-0 top-0 h-20 border-b border-clay/30 bg-clay/10"
          animate={{ y: [0, 430, 0] }}
          transition={{ duration: 9, repeat: Infinity, ease: "easeInOut" }}
        />
        <NotebookSheet className="left-[5%] top-[9%] w-[38%]" delay={0.35} />
        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.55, duration: 0.7, ease: "easeOut" }}
          className="absolute right-[5%] top-[10%] w-[48%] border border-drift bg-[#fffdf9] p-4"
        >
          <div className="flex items-center justify-between border-b border-drift pb-3">
            <div>
              <div className="small-caps text-[11px] text-deep-sea">observatory</div>
              <div className="mt-1 font-mono text-sm text-night-watch">Revenue by region</div>
            </div>
            <LineChart className="h-5 w-5 text-clay" />
          </div>
          <div className="mt-5 flex h-36 items-end gap-2">
            {[42, 76, 58, 94, 68, 88].map((height, idx) => (
              <motion.div
                key={height}
                initial={{ height: 10 }}
                animate={{ height: `${height}%` }}
                transition={{ delay: 0.8 + idx * 0.08, duration: 0.8, ease: "easeOut" }}
                className="flex-1 border border-sea-glass/30 bg-sea-glass/55"
              />
            ))}
          </div>
        </motion.div>
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.85, duration: 0.7, ease: "easeOut" }}
          className="absolute bottom-[8%] left-[15%] right-[9%] border border-night-watch bg-night-watch p-4 text-tidepaper"
        >
          <div className="flex items-center gap-2 border-b border-tidepaper/20 pb-3">
            <MessageSquare className="h-4 w-4 text-clay" />
            <div className="small-caps text-[11px] text-tidepaper/55">grounded answer</div>
          </div>
          <p className="mt-3 max-w-xl font-mono text-sm leading-6 text-tidepaper/85">
            South region variance is tied to discounting. I found it in invoice rows,
            then checked it against the customer table before charting.
          </p>
        </motion.div>
        <motion.div
          className="absolute left-[38%] top-[39%] h-px w-[24%] origin-left bg-clay"
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={{ delay: 1.2, duration: 0.8 }}
        />
        <motion.div
          className="absolute left-[36%] top-[52%] h-px w-[31%] origin-left bg-deep-sea"
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={{ delay: 1.35, duration: 0.8 }}
        />
      </div>
    </div>
  );
}

function NotebookSheet({
  className,
  delay,
}: {
  className: string;
  delay: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 18, rotate: -1 }}
      animate={{ opacity: 1, y: 0, rotate: -1 }}
      transition={{ delay, duration: 0.7, ease: "easeOut" }}
      className={`absolute border border-drift bg-[#fffdf9] p-4 shadow-[0_18px_45px_rgba(44,51,56,0.10)] ${className}`}
    >
      <div className="flex items-center gap-2 border-b border-drift pb-3">
        <FileSpreadsheet className="h-4 w-4 text-deep-sea" />
        <div>
          <div className="small-caps text-[11px] text-night-watch/45">uploaded workbook</div>
          <div className="font-mono text-sm text-night-watch">Operations.xlsx</div>
        </div>
      </div>
      <div className="mt-4 space-y-2">
        {["date", "region", "invoice_total", "customer_id", "discount"].map((field, idx) => (
          <motion.div
            key={field}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: delay + 0.25 + idx * 0.08 }}
            className="grid grid-cols-[minmax(0,1fr)_4rem] gap-3 border border-drift/70 px-3 py-2"
          >
            <span className="truncate font-mono text-xs text-night-watch">{field}</span>
            <span className="small-caps text-[10px] text-night-watch/40">
              {idx === 0 ? "date" : idx === 2 || idx === 4 ? "number" : "text"}
            </span>
          </motion.div>
        ))}
      </div>
    </motion.div>
  );
}

function LandingFact({
  index,
  title,
  body,
  icon: Icon,
  delay = 0,
}: {
  index: string;
  title: string;
  body: string;
  icon?: typeof Database;
  delay?: number;
}) {
  return (
    <motion.div 
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.5 }}
      whileHover={{ backgroundColor: "rgba(226, 221, 209, 0.2)" }}
      transition={{ duration: 0.45, delay }}
      className="group border border-drift bg-tidepaper px-5 py-6"
    >
      <div className="flex items-center justify-between">
        <div className="small-caps text-xs text-deep-sea group-hover:text-sea-glass transition-colors">{index}</div>
        {Icon && <Icon className="h-4 w-4 text-drift group-hover:text-clay transition-colors" />}
      </div>
      <h2 className="mt-6 font-mono text-xl text-night-watch">{title}</h2>
      <p className="mt-3 max-w-sm text-sm leading-relaxed text-night-watch/70">{body}</p>
    </motion.div>
  );
}

function LandingDetail({ label, value }: { label: string; value: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.45 }}
      whileHover={{ x: 4 }}
      className="border border-drift bg-[#fffdf9] px-5 py-5"
    >
      <div className="small-caps text-[11px] text-deep-sea">{label}</div>
      <div className="mt-3 font-mono text-lg text-night-watch">{value}</div>
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
  onSignOut: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const stats = buildWorkspaceStats(sessions, metrics);

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
    <div className="relative flex min-h-full w-full overflow-hidden bg-tidepaper px-7 py-10 text-night-watch">
      <main className="relative z-10 mx-auto flex w-full max-w-7xl flex-col py-4">
        <header className="flex items-center justify-between gap-4 border-b border-drift pb-4">
          <button
            type="button"
            onClick={onBackToLanding}
            className="text-night-watch/60 transition hover:text-deep-sea"
          >
            <CernoLockup markClassName="h-6 w-6" wordmarkClassName="text-base" />
          </button>
          <div className="small-caps text-sm text-night-watch/60">workspaces</div>
        </header>

        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="min-w-0">
            <section className="mt-16 grid gap-12 lg:grid-cols-[minmax(0,1.05fr)_minmax(25rem,0.95fr)]">
              <div>
                <div className="small-caps text-sm text-deep-sea">workspaces</div>
                <h1 className="mt-3 max-w-4xl font-serif text-5xl leading-tight tracking-tight text-night-watch sm:text-6xl">
                  Open a workspace or start with new files.
                </h1>
                <p className="mt-6 max-w-3xl text-lg leading-8 text-night-watch/70">
                  Upload Excel files, ask questions, generate insights, and save useful charts.
                </p>
              </div>

              <div className="grid grid-cols-2 gap-x-10 gap-y-7 border border-drift bg-drift/20 px-5 py-7">
                <WorkspaceStat label="workspaces" value={stats.workspaces} />
                <WorkspaceStat label="files uploaded" value={stats.filesUploaded} />
                <WorkspaceStat label="last activity" value={stats.lastActivity} />
                <WorkspaceStat label="total rows" value={stats.totalRows} />
              </div>
            </section>

            <section className="mx-auto mt-14 w-full max-w-5xl">
              <div className="w-full border border-drift bg-tidepaper p-2 shadow-sm">
                <div className="flex flex-col gap-2 sm:flex-row">
                  <label className="flex min-w-0 flex-1 items-center gap-4 border border-drift bg-drift/20 px-5 py-4 text-left text-night-watch">
                    <span className="small-caps shrink-0 text-sm text-night-watch/60">
                      What are you analyzing?
                    </span>
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      onKeyDown={handleKey}
                      placeholder="Excel files, monthly sales, audit data..."
                      className="min-w-0 flex-1 bg-transparent font-mono text-base text-night-watch placeholder:text-night-watch/40 focus:outline-none"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={handleStart}
                    disabled={starting}
                    className="small-caps bg-night-watch px-7 py-4 text-sm text-tidepaper transition hover:bg-deep-sea disabled:opacity-40"
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
            </section>

            <section className="mx-auto mt-14 w-full max-w-5xl">
              <div className="flex items-end justify-between gap-4 border-b border-drift pb-4">
                <div>
                  <div className="small-caps text-sm text-deep-sea">current workspaces</div>
                  <h2 className="mt-2 font-serif text-2xl text-night-watch">
                    Saved analysis rooms
                  </h2>
                </div>
                <span className="text-sm text-night-watch/60">
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
                <div className="mt-5 border border-dashed border-drift bg-tidepaper px-5 py-8 font-mono text-base text-night-watch/60">
                  No saved workspaces yet.
                </div>
              )}
            </section>
          </div>

          <UserProfilePanel user={user} onSignOut={onSignOut} />
        </div>
      </main>
    </div>
  );
}

function UserProfilePanel({
  user,
  onSignOut,
}: {
  user: CurrentUser;
  onSignOut: () => Promise<void>;
}) {
  const displayName = userDisplayName(user);

  return (
    <aside className="mt-16 h-fit border border-drift bg-drift/20 p-4 lg:sticky lg:top-10">
      <div className="flex items-center gap-3 border-b border-drift pb-4">
        {user.picture ? (
          <img
            src={user.picture}
            alt=""
            className="h-12 w-12 border border-drift object-cover"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="flex h-12 w-12 items-center justify-center border border-drift bg-tidepaper font-mono text-base text-deep-sea">
            {userInitials(displayName)}
          </div>
        )}
        <div className="min-w-0">
          <div className="truncate font-mono text-base text-night-watch">{displayName}</div>
          <div className="truncate text-sm text-night-watch/55">{user.email}</div>
        </div>
      </div>

      <section className="border-b border-drift py-5">
        <div className="small-caps text-sm text-night-watch/60">usage stats</div>
        <div className="mt-4 h-24 border border-dashed border-drift bg-tidepaper/60" />
      </section>

      <div className="grid gap-2 pt-4">
        <button
          type="button"
          className="small-caps border border-deep-sea/30 bg-tidepaper px-4 py-3 text-sm text-deep-sea transition hover:border-deep-sea hover:bg-sea-glass/10"
        >
          upgrade plan
        </button>
        <button
          type="button"
          onClick={onSignOut}
          className="small-caps border border-red-900/20 bg-red-50 px-4 py-3 text-sm text-red-600 transition hover:border-red-900/40 hover:bg-red-100"
        >
          sign out
        </button>
      </div>
    </aside>
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
      <div className="small-caps text-sm text-neutral-500">{label}</div>
      <div className="mt-2 font-mono text-3xl text-ink">{value}</div>
    </div>
  );
}

function WorkspaceLoadingPanel({ activeTab }: { activeTab: TabKey }) {
  const title =
    activeTab === "ask"
      ? "Loading chat"
      : activeTab === "insights"
        ? "Loading insights"
        : "Loading files";

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
      <div
        className="group grid gap-4 border border-drift bg-tidepaper px-5 py-5 transition hover:border-deep-sea hover:bg-drift/30 md:grid-cols-[minmax(0,1fr)_auto]"
        onMouseEnter={onPrefetch}
        onFocus={onPrefetch}
      >
        <button
          type="button"
          onClick={onResume}
          className="min-w-0 text-left"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            <span className="h-3 w-3 bg-sea-glass" />
            <span className="truncate font-mono text-xl text-night-watch">{session.name}</span>
            <span className="small-caps border border-drift bg-tidepaper px-2 py-1 text-sm text-night-watch/70">
              {workspaceStatusLabel(session)}
            </span>
          </div>
          <div className="mt-4 grid gap-4 font-mono text-sm text-night-watch/70 sm:grid-cols-4">
            <WorkspaceFact label="files" value={formatMaybeNumber(fileCount)} />
            <WorkspaceFact label="rows" value={formatMaybeNumber(rowCount)} />
            <WorkspaceFact label="last activity" value={formatActivity(activity)} />
            <WorkspaceFact
              label="created"
              value={formatActivity(session.created_at)}
            />
          </div>
        </button>
        <div className="flex items-center gap-2 md:flex-col md:items-end md:justify-between">
          <button
            type="button"
            onClick={onResume}
            className="small-caps bg-night-watch px-4 py-2 text-sm text-tidepaper transition hover:bg-deep-sea"
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
      </div>
    </li>
  );
}

function WorkspaceFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="small-caps text-sm text-neutral-400">{label}</div>
      <div className="mt-1 text-base text-ink">{value}</div>
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
