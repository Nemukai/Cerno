import { useCallback, useEffect, useRef, useState } from "react";
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
  listFiles,
  listLinks,
  listSessions,
  postChat,
  processSession,
  updateTurn,
  uploadFiles,
} from "./lib/api";
import type {
  ChatArtifact,
  ChatMessage,
  ChatTurn,
  DataDoc,
  DiscoveredFile,
  DiscoveredLink,
  DiscoveryResponse,
  FileRecord,
  Link,
  ProcessingEvent,
  Session,
} from "./lib/types";
import { ChatSidebar } from "./components/ChatSidebar";
import { CernoLockup } from "./components/Brand";
import {
  FilesPanel,
  InsightsPanel,
  WorkspaceSidebar,
} from "./components/SessionPanels";
import { SessionHeader } from "./components/SessionHeader";
import { Shell, type TabKey } from "./components/Shell";
import { AuthGate } from "./components/AuthGate";
import { BetaGate } from "./components/BetaGate";
import { useUser, googleLoginUrl } from "./lib/auth";

type WorkspaceMetric = {
  fileCount: number;
  rowCount: number;
  lastOpenedAt: string | null;
  loaded: boolean;
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
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
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

  const refreshChatFeed = useCallback(async (sessionId: string) => {
    const feed = await getChatFeed(sessionId);
    setTurns(feed.map((item) => item.turn));
    setMessagesByTurn(
      Object.fromEntries(feed.map((item) => [item.turn.id, item.messages])),
    );
    setArtifactsByTurn(
      Object.fromEntries(feed.map((item) => [item.turn.id, item.artifacts])),
    );
  }, []);

  const refreshSchemaGuide = useCallback(async (sessionId: string) => {
    try {
      const doc = await getSchemaGuide(sessionId);
      setDataDoc(doc);
    } catch {
      setDataDoc(null);
    }
  }, []);

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
  }, [session, sessions]);

  useEffect(() => {
    if (!session) return;
    const id = session.id;
    Promise.all([
      refreshFiles(id),
      refreshLinks(id),
      refreshDiscovery(id),
      refreshEvents(id),
      refreshChatFeed(id),
      refreshSchemaGuide(id),
    ]).catch((err: Error) => setError(err.message));
  }, [
    session,
    refreshFiles,
    refreshLinks,
    refreshDiscovery,
    refreshEvents,
    refreshChatFeed,
    refreshSchemaGuide,
  ]);

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
      clearSessionState();
      markWorkspaceOpened(nextSession.id);
      setSession(nextSession);
    }
    if (activeTab !== route.tab) {
      setActiveTab(route.tab);
    }
  }, [
    activeTab,
    clearSessionState,
    navigate,
    route,
    session,
    sessions,
    sessionsLoaded,
  ]);

  const handleCreateSession = useCallback(
    async (name: string) => {
      setError(null);
      setStarting(true);
      try {
        const s = await createSession(name);
        clearSessionState();
        setHomeView("sessions");
        markWorkspaceOpened(s.id);
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
      clearSessionState();
      setHomeView("sessions");
      markWorkspaceOpened(s.id);
      setSession(s);
      setActiveTab("ask");
      navigateSessionTab(s.id, "ask");
    },
    [clearSessionState, navigateSessionTab],
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
        const result = await uploadFiles(session.id, deduped);
        const queuedEvents = result.jobs.flatMap((job) => job.events);
        if (queuedEvents.length > 0) {
          setEvents(queuedEvents);
          setProcessing(true);
        } else {
          setEvents([]);
        }
        setDiscovery(null);
        setLinks([]);
        setDataDoc(null);
        await Promise.all([
          refreshFiles(session.id),
          refreshSessions(),
        ]);
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
    ],
  );

  const handleSend = useCallback(
    async (message: string) => {
      if (!session) return;
      setError(null);
      setSending(true);
      try {
        const response = await postChat(session.id, message);
        setActiveTurnId(response.turn_id);
        await refreshChatFeed(session.id);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setSending(false);
      }
    },
    [session, refreshChatFeed],
  );

  const handleNewChat = useCallback(() => {
    if (session) navigateSessionTab(session.id, "ask");
    setActiveTab("ask");
    setActiveTurnId(null);
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
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [session, refreshChatFeed],
  );

  const handleDeleteTurn = useCallback(
    async (turnId: string) => {
      if (!session) return;
      setError(null);
      try {
        await deleteTurn(turnId);
        if (activeTurnId === turnId) setActiveTurnId(null);
        await refreshChatFeed(session.id);
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [activeTurnId, session, refreshChatFeed],
  );

  const handleTabChange = useCallback(
    (tab: TabKey) => {
      if (session) navigateSessionTab(session.id, tab);
      setActiveTab(tab);
    },
    [navigateSessionTab, session],
  );

  const discoveryStatus = discovery?.status ?? "empty";

  if (!session) {
    if (homeView === "landing") {
      return <LandingPage onEnter={() => navigateHome("sessions")} />;
    }
    return (
      <AuthGate>
        {(user, _signOut, onUserUpdate) => (
          <BetaGate user={user} onUserUpdate={onUserUpdate}>
            <WorkspacesPage
              sessions={sessions}
              onStart={handleCreateSession}
              onResume={handleResumeSession}
              onDelete={handleDeleteSession}
              metrics={workspaceMetrics}
              starting={starting}
              error={error}
              onDismissError={() => setError(null)}
              onBackToLanding={() => navigateHome("landing")}
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
            showFileActions={files.length > 0}
          />
        }
        activeTab={activeTab}
        onTabChange={handleTabChange}
        onDropFile={handleUpload}
        showTabs={files.length > 0}
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((current) => !current)}
      >
        {files.length === 0 ? (
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
        {files.length > 0 && activeTab === "ask" ? (
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
            onSend={handleSend}
            onOpenInsights={() => handleTabChange("insights")}
            onOpenFiles={() => handleTabChange("files")}
          />
        ) : null}
        {files.length > 0 && activeTab === "insights" ? (
          <InsightsPanel
            files={files}
            links={links}
            discovery={discovery}
            dataDoc={dataDoc}
            events={events}
            processing={processing}
            approving={approving}
            onProcess={handleProcess}
            onApprove={handleApprove}
            onOpenFiles={() => handleTabChange("files")}
          />
        ) : null}
        {files.length > 0 && activeTab === "files" ? (
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
        <header className="flex items-center justify-between px-6 py-5 sm:px-10 border-b border-drift bg-tidepaper/80 backdrop-blur-md">
          <CernoLockup markClassName="h-6 w-6 text-deep-sea" wordmarkClassName="text-base text-night-watch" />
          <a
            href={googleLoginUrl()}
            className="small-caps border border-drift bg-tidepaper px-3 py-1.5 text-xs text-night-watch transition hover:bg-drift"
          >
            login
          </a>
        </header>

        <section className="flex flex-1 items-center px-6 pb-16 pt-12 sm:px-10 relative z-10">
          <div className="w-full max-w-7xl mx-auto grid lg:grid-cols-2 gap-16 items-center">
            <motion.div 
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, ease: "easeOut" }}
              className="border-l border-drift pl-8"
            >
              <motion.div 
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.3 }}
                className="flex items-center gap-2 small-caps text-xs text-deep-sea"
              >
                <Sparkles className="h-3 w-3" /> measured & tactile
              </motion.div>
              <h1 className="mt-5 max-w-2xl font-serif text-5xl leading-[1.02] text-night-watch sm:text-7xl lg:text-7xl">
                Research notebook meets observatory.
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-8 text-night-watch/70">
                Upload spreadsheets, approve the data map, then work from a chat
                that understands the files before it answers.
              </p>
              <div className="mt-10 flex flex-wrap items-center gap-4">
                <a
                  href={googleLoginUrl()}
                  className="group flex items-center gap-2 small-caps bg-night-watch px-6 py-3.5 text-sm text-tidepaper transition hover:bg-deep-sea"
                >
                  sign in to continue
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                </a>
              </div>
            </motion.div>

            <motion.div 
              initial={{ opacity: 0, x: 40 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 1, delay: 0.2, ease: "easeOut" }}
              className="hidden lg:block relative h-[500px] w-full"
            >
              {/* Abstract Mockup Elements */}
              <div className="absolute inset-0 grid grid-cols-6 grid-rows-6 gap-4 p-4 border border-drift bg-tidepaper/50 backdrop-blur-sm">
                <motion.div 
                   initial={{ height: 0 }}
                   animate={{ height: "100%" }}
                   transition={{ duration: 1.5, delay: 0.5, ease: "easeInOut" }}
                   className="col-span-2 row-span-4 border border-drift bg-drift/20 p-4 flex flex-col gap-4 overflow-hidden"
                >
                   <div className="h-2 w-1/2 bg-drift" />
                   <div className="h-2 w-3/4 bg-drift" />
                   <div className="flex-1 border border-drift/50 flex items-center justify-center text-drift">
                      <Database className="h-8 w-8" />
                   </div>
                </motion.div>
                <motion.div 
                   initial={{ opacity: 0, scale: 0.95 }}
                   animate={{ opacity: 1, scale: 1 }}
                   transition={{ duration: 1, delay: 0.8, ease: "easeOut" }}
                   className="col-span-4 row-span-3 border border-drift bg-tidepaper p-4 shadow-sm flex flex-col gap-3 relative"
                >
                  <div className="flex items-center gap-3 border-b border-drift pb-3">
                     <MessageSquare className="h-4 w-4 text-deep-sea" />
                     <div className="h-2 w-1/3 bg-drift" />
                  </div>
                  <div className="flex-1 flex items-end gap-2 px-2">
                     <motion.div initial={{ height: "20%" }} animate={{ height: "40%" }} transition={{ duration: 1, delay: 1 }} className="flex-1 bg-sea-glass/40 border border-sea-glass/20" />
                     <motion.div initial={{ height: "30%" }} animate={{ height: "70%" }} transition={{ duration: 1, delay: 1.2 }} className="flex-1 bg-sea-glass/60 border border-sea-glass/30" />
                     <motion.div initial={{ height: "40%" }} animate={{ height: "100%" }} transition={{ duration: 1, delay: 1.4 }} className="flex-1 bg-sea-glass border border-sea-glass" />
                     <motion.div initial={{ height: "20%" }} animate={{ height: "60%" }} transition={{ duration: 1, delay: 1.6 }} className="flex-1 bg-sea-glass/80 border border-sea-glass/40" />
                  </div>
                </motion.div>
                <motion.div 
                   initial={{ opacity: 0, y: 20 }}
                   animate={{ opacity: 1, y: 0 }}
                   transition={{ duration: 0.8, delay: 1.2, ease: "easeOut" }}
                   className="col-span-4 row-span-3 border border-drift bg-tidepaper p-4 shadow-sm"
                >
                  <div className="h-2 w-1/4 bg-drift mb-4" />
                  <div className="space-y-3">
                     <div className="h-8 w-full border border-drift flex items-center px-3 gap-3">
                        <FileSpreadsheet className="h-3 w-3 text-deep-sea" />
                        <div className="h-1 w-1/2 bg-drift" />
                     </div>
                     <div className="h-8 w-full border border-drift flex items-center px-3 gap-3">
                        <FileSpreadsheet className="h-3 w-3 text-deep-sea" />
                        <div className="h-1 w-1/3 bg-drift" />
                     </div>
                  </div>
                </motion.div>
                <motion.div 
                   initial={{ opacity: 0 }}
                   animate={{ opacity: 1 }}
                   transition={{ duration: 1, delay: 1.5 }}
                   className="col-span-2 row-span-2 border border-drift bg-night-watch text-tidepaper p-4 flex flex-col justify-between"
                >
                   <LineChart className="h-5 w-5 text-clay" />
                   <div>
                     <div className="text-2xl font-mono">84%</div>
                     <div className="text-[10px] small-caps text-tidepaper/60 mt-1">confidence</div>
                   </div>
                </motion.div>
              </div>
            </motion.div>
          </div>
        </section>

        <section className="relative z-10 grid border-t border-drift bg-tidepaper md:grid-cols-3">
          <LandingFact
            index="01"
            title="Map first"
            body="Headers, file meaning, relationships, and caveats are reviewed before analysis."
            icon={Database}
          />
          <LandingFact
            index="02"
            title="Chat visuals next"
            body="Approved workspaces answer questions with charts, KPIs, and tables directly in chat."
            icon={LineChart}
          />
          <LandingFact
            index="03"
            title="Chat stays grounded"
            body="Questions use the approved schema guide and render new views when visuals help."
            icon={MessageSquare}
          />
        </section>
      </main>
    </div>
  );
}

function LandingFact({
  index,
  title,
  body,
  icon: Icon,
}: {
  index: string;
  title: string;
  body: string;
  icon?: any;
}) {
  return (
    <motion.div 
      whileHover={{ backgroundColor: "rgba(226, 221, 209, 0.2)" }}
      transition={{ duration: 0.2 }}
      className="border-b border-drift px-6 py-8 md:border-b-0 md:border-r md:last:border-r-0 lg:px-10 group"
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

function WorkspacesPage({
  sessions,
  onStart,
  onResume,
  onDelete,
  metrics,
  starting,
  error,
  onDismissError,
  onBackToLanding,
}: {
  sessions: Session[];
  onStart: (name: string) => void;
  onResume: (session: Session) => void;
  onDelete: (id: string) => void;
  metrics: Record<string, WorkspaceMetric>;
  starting: boolean;
  error: string | null;
  onDismissError: () => void;
  onBackToLanding: () => void;
}) {
  const [name, setName] = useState("");
  const stats = buildWorkspaceStats(sessions, metrics);

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
              <label className="flex min-w-0 flex-1 items-center gap-4 bg-drift/20 px-5 py-4 text-left text-night-watch border border-drift">
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
                />
              ))}
            </ul>
          ) : (
            <div className="mt-5 border border-dashed border-drift bg-tidepaper px-5 py-8 font-mono text-base text-night-watch/60">
              No saved workspaces yet.
            </div>
          )}
        </section>
      </main>
    </div>
  );
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

function WorkspaceRow({
  session,
  metric,
  onResume,
  onDelete,
}: {
  session: Session;
  metric?: WorkspaceMetric;
  onResume: () => void;
  onDelete: () => void;
}) {
  const activity = workspaceActivity(session, metric);
  const fileCount = metric?.loaded ? metric.fileCount : null;
  const rowCount = metric?.loaded ? metric.rowCount : null;

  return (
    <li>
      <div className="group grid gap-4 border border-drift bg-tidepaper px-5 py-5 transition hover:border-deep-sea hover:bg-drift/30 md:grid-cols-[minmax(0,1fr)_auto]">
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
