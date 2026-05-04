import { useCallback, useEffect, useRef, useState } from "react";
import {
  approveSchema,
  buildDashboard,
  createSession,
  deleteFile,
  deleteSession,
  getDashboard,
  getDiscovery,
  getProcessingEvents,
  getSchemaGuide,
  listFiles,
  listLinks,
  listSessions,
  listTurns,
  postChat,
  processSession,
  uploadFiles,
} from "./lib/api";
import type {
  ChatTurn,
  DataDoc,
  DashboardPage,
  DiscoveredFile,
  DiscoveredLink,
  DiscoveryResponse,
  DashboardCell,
  FileRecord,
  Link,
  ProcessingEvent,
  Session,
} from "./lib/types";
import { ChatSidebar } from "./components/ChatSidebar";
import { CernoLockup, CernoMark, CernoWordmark } from "./components/Brand";
import { DashboardTab } from "./components/DashboardTab";
import { SchemaTab } from "./components/SchemaTab";
import { SessionHeader } from "./components/SessionHeader";
import { Shell, type TabKey } from "./components/Shell";

type WorkspaceMetric = {
  fileCount: number;
  rowCount: number;
  lastOpenedAt: string | null;
  loaded: boolean;
};

export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [homeView, setHomeView] = useState<"landing" | "sessions">("landing");
  const [session, setSession] = useState<Session | null>(null);
  const [workspaceMetrics, setWorkspaceMetrics] = useState<
    Record<string, WorkspaceMetric>
  >({});
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [discovery, setDiscovery] = useState<DiscoveryResponse | null>(null);
  const [events, setEvents] = useState<ProcessingEvent[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [dataDoc, setDataDoc] = useState<DataDoc | null>(null);
  const [pages, setPages] = useState<DashboardPage[]>([]);
  const [cellsByPage, setCellsByPage] = useState<Record<string, DashboardCell[]>>(
    {},
  );
  const [dashboardLoadedFor, setDashboardLoadedFor] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("dashboard");
  const [focusPageId, setFocusPageId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [approving, setApproving] = useState(false);
  const [sending, setSending] = useState(false);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bootstrapped = useRef(false);
  const pollRef = useRef<number | null>(null);
  const autoBuildSessions = useRef<Set<string>>(new Set());

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

  const refreshDashboard = useCallback(async (sessionId: string) => {
    const dash = await getDashboard(sessionId);
    setPages(dash.pages);
    setCellsByPage(dash.cells_by_page);
    setDashboardLoadedFor(sessionId);
  }, []);

  const refreshTurns = useCallback(async (sessionId: string) => {
    const ts = await listTurns(sessionId);
    setTurns(ts);
  }, []);

  const refreshSchemaGuide = useCallback(async (sessionId: string) => {
    try {
      const doc = await getSchemaGuide(sessionId);
      setDataDoc(doc);
    } catch {
      setDataDoc(null);
    }
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
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    refreshSessions().catch((err: Error) => setError(err.message));
  }, [refreshSessions]);

  useEffect(() => {
    if (!session) return;
    const id = session.id;
    Promise.all([
      refreshFiles(id),
      refreshLinks(id),
      refreshDiscovery(id),
      refreshEvents(id),
      refreshDashboard(id),
      refreshTurns(id),
      refreshSchemaGuide(id),
    ]).catch((err: Error) => setError(err.message));
  }, [
    session,
    refreshFiles,
    refreshLinks,
    refreshDiscovery,
    refreshEvents,
    refreshDashboard,
    refreshTurns,
    refreshSchemaGuide,
  ]);

  useEffect(() => {
    if (!session || !processing) {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    const id = session.id;
    pollRef.current = window.setInterval(() => {
      refreshEvents(id).catch(() => undefined);
    }, 1500);
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [session, processing, refreshEvents]);

  const clearSessionState = useCallback(() => {
    setFiles([]);
    setLinks([]);
    setDiscovery(null);
    setEvents([]);
    setTurns([]);
    setDataDoc(null);
    setPages([]);
    setCellsByPage({});
    setDashboardLoadedFor(null);
    setActiveTab("dashboard");
    setFocusPageId(null);
  }, []);

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
        await refreshSessions();
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setStarting(false);
      }
    },
    [clearSessionState, refreshSessions],
  );

  const handleResumeSession = useCallback(
    (s: Session) => {
      clearSessionState();
      setHomeView("sessions");
      markWorkspaceOpened(s.id);
      setSession(s);
    },
    [clearSessionState],
  );

  const handleHome = useCallback(() => {
    setSession(null);
    setHomeView("sessions");
    clearSessionState();
    refreshSessions().catch((err: Error) => setError(err.message));
  }, [clearSessionState, refreshSessions]);

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
        }
        await refreshSessions();
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [session, clearSessionState, refreshSessions],
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
      try {
        await uploadFiles(session.id, deduped);
        setPages([]);
        setCellsByPage({});
        setDashboardLoadedFor(session.id);
        await Promise.all([
          refreshFiles(session.id),
          refreshDiscovery(session.id),
        ]);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setUploading(false);
      }
    },
    [session, refreshFiles, refreshDiscovery],
  );

  const handleDeleteFile = useCallback(
    async (fileId: string) => {
      if (!session) return;
      setError(null);
      try {
        await deleteFile(fileId);
        setPages([]);
        setCellsByPage({});
        setDashboardLoadedFor(session.id);
        await Promise.all([
          refreshFiles(session.id),
          refreshDiscovery(session.id),
          refreshLinks(session.id),
        ]);
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [session, refreshFiles, refreshDiscovery, refreshLinks],
  );

  const handleProcess = useCallback(async () => {
    if (!session) return;
    setError(null);
    setProcessing(true);
    setEvents([]);
    setActiveTab("schema");
    setPages([]);
    setCellsByPage({});
    setDashboardLoadedFor(session.id);
    try {
      const result = await processSession(session.id);
      setDiscovery(result);
      await Promise.all([
        refreshFiles(session.id),
        refreshLinks(session.id),
        refreshEvents(session.id),
        refreshSchemaGuide(session.id),
      ]);
    } catch (err) {
      setError((err as Error).message);
      await refreshDiscovery(session.id).catch(() => undefined);
      await refreshEvents(session.id).catch(() => undefined);
    } finally {
      setProcessing(false);
    }
  }, [session, refreshFiles, refreshLinks, refreshDiscovery, refreshEvents, refreshSchemaGuide]);

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
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setApproving(false);
      }
    },
    [session, refreshFiles, refreshLinks, refreshSchemaGuide, refreshSessions],
  );

  const handleSend = useCallback(
    async (message: string) => {
      if (!session) return;
      setError(null);
      setSending(true);
      try {
        await postChat(session.id, message);
        await Promise.all([
          refreshTurns(session.id),
          refreshDashboard(session.id),
        ]);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setSending(false);
      }
    },
    [session, refreshTurns, refreshDashboard],
  );

  const handleBuildDashboard = useCallback(async () => {
    if (!session) return;
    setError(null);
    setBuilding(true);
    try {
      await buildDashboard(session.id);
      await refreshDashboard(session.id);
      setActiveTab("dashboard");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBuilding(false);
    }
  }, [session, refreshDashboard]);

  const handleOpenPage = useCallback((pageId: string) => {
    setActiveTab("dashboard");
    setFocusPageId(pageId);
  }, []);

  const discoveryStatus = discovery?.status ?? "empty";

  useEffect(() => {
    if (!session) return;
    if (discoveryStatus !== "approved") return;
    if (dashboardLoadedFor !== session.id) return;
    if (pages.length > 0 || building) return;
    if (autoBuildSessions.current.has(session.id)) return;
    autoBuildSessions.current.add(session.id);
    handleBuildDashboard().catch((err: Error) => setError(err.message));
  }, [
    session,
    discoveryStatus,
    dashboardLoadedFor,
    pages.length,
    building,
    handleBuildDashboard,
  ]);

  if (!session) {
    if (homeView === "landing") {
      return (
        <LandingPage
          sessionCount={sessions.length}
          onEnter={() => setHomeView("sessions")}
        />
      );
    }
    return (
      <WorkspacesPage
        sessions={sessions}
        onStart={handleCreateSession}
        onResume={handleResumeSession}
        onDelete={handleDeleteSession}
        metrics={workspaceMetrics}
        starting={starting}
        error={error}
        onDismissError={() => setError(null)}
        onBackToLanding={() => setHomeView("landing")}
      />
    );
  }

  const chatReady = discoveryStatus === "approved";

  return (
    <>
      <Shell
        sidebar={
          <ChatSidebar
            turns={turns}
            pages={pages}
            disabled={!chatReady}
            sending={sending}
            onSend={handleSend}
            onOpenPage={handleOpenPage}
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
          />
        }
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onDropFile={handleUpload}
      >
        {activeTab === "dashboard" ? (
          <DashboardTab
            pages={pages}
            cellsByPage={cellsByPage}
            focusPageId={focusPageId}
            files={files}
            discoveryStatus={discoveryStatus}
            onUpload={handleUpload}
            uploading={uploading}
            onDeleteFile={handleDeleteFile}
            onProcess={handleProcess}
            processing={processing}
            onBuildDashboard={handleBuildDashboard}
            building={building}
            onReviewSchema={() => setActiveTab("schema")}
          />
        ) : null}
        {activeTab === "schema" ? (
          <SchemaTab
            files={files}
            links={links}
            discovery={discovery}
            doc={dataDoc}
            events={events}
            processing={processing}
            approving={approving}
            onProcess={handleProcess}
            onApprove={handleApprove}
            canProcess={files.length >= 1}
          />
        ) : null}
      </Shell>
      {error ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-4 flex justify-center">
          <div className="pointer-events-auto border border-ink bg-white px-3 py-2 font-mono text-xs text-red-600">
            {error}
            <button
              type="button"
              onClick={() => setError(null)}
              className="ml-3 text-neutral-500 hover:text-ink"
            >
              {"\u00d7"}
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}

function LandingPage({
  sessionCount,
  onEnter,
}: {
  sessionCount: number;
  onEnter: () => void;
}) {
  const [pointer, setPointer] = useState({ x: 0.5, y: 0.5 });

  const handlePointerMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setPointer({
      x: (e.clientX - rect.left) / rect.width,
      y: (e.clientY - rect.top) / rect.height,
    });
  };

  const asciiRows = [
    "CERNO::DATA_MAP   raw rows -> headers -> relationships",
    "schema guide / dashboard signals / analyst chat",
    "xlsx + csv ::::: profile ::::: approve ::::: inspect",
    "joins: detected   caveats: written   charts: generated",
    "source files -> shared meaning -> working dashboard",
    "01000011 01000101 01010010 01001110 01001111",
  ];

  return (
    <div
      className="relative min-h-full overflow-hidden bg-paper text-ink"
      onMouseMove={handlePointerMove}
    >
      <div className="pointer-events-none absolute inset-0">
        <div
          className="absolute inset-y-0 w-40 bg-ember/10 blur-3xl transition-transform duration-300 ease-out"
          style={{
            left: `${pointer.x * 100}%`,
            transform: "translateX(-50%) skewX(-10deg)",
          }}
        />
        <div
          className="absolute inset-0 bg-[linear-gradient(115deg,transparent_0%,rgba(232,93,35,0.12)_var(--scan),transparent_calc(var(--scan)_+_16%))]"
          style={{ "--scan": `${pointer.x * 100}%` } as React.CSSProperties}
        />
        <div className="absolute inset-0 flex select-none flex-col justify-around py-8 font-mono text-[10px] uppercase leading-loose text-ember/20 sm:text-xs lg:text-sm">
          {Array.from({ length: 20 }).map((_, i) => {
            const direction = i % 2 === 0 ? 1 : -1;
            const driftX = (pointer.x - 0.5) * direction * (20 + (i % 4) * 5);
            const driftY = (pointer.y - 0.5) * direction * 8;
            return (
              <div
                key={i}
                className="whitespace-nowrap transition-transform duration-300 ease-out"
                style={{
                  transform: `translate3d(${driftX}px, ${driftY}px, 0)`,
                  opacity: 0.12 + (i % 5) * 0.035,
                }}
              >
                {asciiRows[i % asciiRows.length]}{" "}
                {asciiRows[(i + 2) % asciiRows.length]}
              </div>
            );
          })}
        </div>
      </div>

      <main className="relative z-10 flex min-h-full flex-col">
        <header className="flex items-center justify-between px-6 py-5 sm:px-10">
          <CernoLockup markClassName="h-6 w-6" wordmarkClassName="text-base text-ink" />
          <button
            type="button"
            onClick={onEnter}
            className="small-caps border border-ink bg-white/70 px-3 py-1.5 text-xs text-ink backdrop-blur transition hover:border-ember hover:text-ember"
          >
            sessions
          </button>
        </header>

        <section className="flex flex-1 items-center px-6 pb-16 pt-8 sm:px-10">
          <div className="max-w-5xl">
            <div className="small-caps text-xs text-ember">private data workspace</div>
            <h1 className="mt-5 flex max-w-4xl items-center gap-5 text-5xl leading-[1.02] text-ink sm:text-7xl lg:text-8xl">
              <CernoMark className="h-14 w-14 shrink-0 sm:h-20 sm:w-20 lg:h-24 lg:w-24" />
              <CernoWordmark />
            </h1>
            <p className="mt-5 max-w-2xl text-lg leading-8 text-neutral-600">
              Upload spreadsheets, approve the data map, then work from a dashboard
              that understands the files before it answers.
            </p>
            <div className="mt-10 flex flex-wrap items-center gap-4">
              <button
                type="button"
                onClick={onEnter}
                className="small-caps bg-ember px-5 py-3 text-xs text-white transition hover:bg-ember-hover"
              >
                enter workspace
              </button>
              <div className="font-mono text-xs text-neutral-500">
                {sessionCount} saved workspace{sessionCount === 1 ? "" : "s"}
              </div>
            </div>
          </div>
        </section>

        <section className="relative z-10 grid border-t border-ink/10 bg-white/55 backdrop-blur md:grid-cols-3">
          <LandingFact
            index="01"
            title="Map first"
            body="Headers, file meaning, relationships, and caveats are reviewed before analysis."
          />
          <LandingFact
            index="02"
            title="Dashboard next"
            body="Approved workspaces open into generated views that can be reshaped for the task."
          />
          <LandingFact
            index="03"
            title="Chat stays grounded"
            body="Questions use the approved schema guide and render new views when visuals help."
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
}: {
  index: string;
  title: string;
  body: string;
}) {
  return (
    <div className="border-b border-ink/10 px-6 py-5 md:border-b-0 md:border-r md:last:border-r-0 lg:px-10">
      <div className="small-caps text-xs text-ember">{index}</div>
      <h2 className="mt-2 font-mono text-lg text-ink">{title}</h2>
      <p className="mt-2 max-w-sm text-sm leading-6 text-neutral-600">{body}</p>
    </div>
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
  const [pointer, setPointer] = useState({ x: 0.5, y: 0.5 });
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
      `Delete workspace "${s.name}"? Files, links, dashboards, and chat history will be removed. This cannot be undone.`,
    );
    if (ok) onDelete(s.id);
  };

  const handlePointerMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setPointer({
      x: (e.clientX - rect.left) / rect.width,
      y: (e.clientY - rect.top) / rect.height,
    });
  };

  return (
    <div
      className="relative flex min-h-full w-full overflow-hidden bg-[#fff8f1] px-7 py-10 text-ink"
      onMouseMove={handlePointerMove}
    >
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.92),transparent_34%),linear-gradient(180deg,rgba(255,247,238,0.95),rgba(250,246,238,0.88))]" />
        <div
          className="absolute h-72 w-72 rounded-full bg-ember/20 blur-3xl transition-transform duration-300 ease-out"
          style={{
            left: `${pointer.x * 100}%`,
            top: `${pointer.y * 100}%`,
            transform: "translate(-50%, -50%)",
          }}
        />
        <div
          className="absolute h-[28rem] w-[28rem] rounded-full bg-orange-200/25 blur-3xl transition-transform duration-500 ease-out"
          style={{
            left: `${100 - pointer.x * 45}%`,
            top: `${18 + pointer.y * 24}%`,
            transform: "translate(-50%, -50%)",
          }}
        />
      </div>

      <main className="relative z-10 mx-auto flex w-full max-w-7xl flex-col py-4">
        <header className="flex items-center justify-between gap-4">
          <button
            type="button"
            onClick={onBackToLanding}
            className="text-neutral-500 transition hover:text-ember"
          >
            <CernoLockup markClassName="h-6 w-6" wordmarkClassName="text-base" />
          </button>
          <div className="small-caps text-sm text-neutral-500">workspaces</div>
        </header>

        <section className="mt-16 grid gap-12 lg:grid-cols-[minmax(0,1.05fr)_minmax(25rem,0.95fr)]">
          <div>
            <div className="small-caps text-sm text-ember">workspaces</div>
            <h1 className="mt-3 max-w-4xl font-mono text-5xl leading-tight tracking-tight text-ink sm:text-6xl">
              Open a workspace or start with new files.
            </h1>
            <p className="mt-6 max-w-3xl text-lg leading-8 text-neutral-600">
              Upload Excel files, ask questions, generate insights, and save useful charts.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-x-10 gap-y-7 border-y border-ink/10 bg-white/40 px-5 py-7 backdrop-blur">
            <WorkspaceStat label="workspaces" value={stats.workspaces} />
            <WorkspaceStat label="files uploaded" value={stats.filesUploaded} />
            <WorkspaceStat label="last activity" value={stats.lastActivity} />
            <WorkspaceStat label="total rows" value={stats.totalRows} />
          </div>
        </section>

        <section className="mx-auto mt-14 w-full max-w-5xl">
          <div className="w-full border border-ember/50 bg-white/90 p-2 shadow-[0_24px_80px_rgba(232,93,35,0.18)] backdrop-blur">
            <div className="flex flex-col gap-2 sm:flex-row">
              <label className="flex min-w-0 flex-1 items-center gap-4 bg-[#fffaf5] px-5 py-4 text-left text-ink">
                <span className="small-caps shrink-0 text-sm text-neutral-500">
                  What are you analyzing?
                </span>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={handleKey}
                  placeholder="Excel files, monthly sales, audit data..."
                  className="min-w-0 flex-1 bg-transparent text-base text-ink placeholder:text-neutral-400 focus:outline-none"
                />
              </label>
              <button
                type="button"
                onClick={handleStart}
                disabled={starting}
                className="small-caps bg-ember px-7 py-4 text-sm text-white transition hover:bg-ember-hover disabled:opacity-40"
              >
                {starting ? "starting..." : "start workspace"}
              </button>
            </div>
          </div>

          {error ? (
            <div className="mt-4 flex w-full items-start justify-between gap-3 border border-red-300 bg-white/90 px-3 py-2 font-mono text-xs text-red-600">
              <span>{error}</span>
              <button
                type="button"
                onClick={onDismissError}
                className="text-neutral-500 hover:text-ink"
              >
                x
              </button>
            </div>
          ) : null}
        </section>

        <section className="mx-auto mt-14 w-full max-w-5xl">
          <div className="flex items-end justify-between gap-4 border-b border-ink/10 pb-4">
            <div>
              <div className="small-caps text-sm text-ember/80">current workspaces</div>
              <h2 className="mt-2 font-mono text-2xl text-ink">
                Saved analysis rooms
              </h2>
            </div>
            <span className="text-sm text-neutral-500">
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
            <div className="mt-5 border border-dashed border-ink/15 bg-white/70 px-5 py-8 text-base text-neutral-500">
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
      <div className="group grid gap-4 border border-ink/10 bg-white/90 px-5 py-5 shadow-[0_14px_45px_rgba(80,45,20,0.06)] backdrop-blur transition hover:border-ember/60 hover:bg-white md:grid-cols-[minmax(0,1fr)_auto]">
        <button
          type="button"
          onClick={onResume}
          className="min-w-0 text-left"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            <span className="h-3 w-3 bg-ember shadow-[0_0_24px_rgba(232,93,35,0.45)]" />
            <span className="truncate font-mono text-xl text-ink">{session.name}</span>
            <span className="small-caps border border-neutral-200 bg-[#fff8f1] px-2 py-1 text-sm text-neutral-600">
              {workspaceStatusLabel(session)}
            </span>
          </div>
          <div className="mt-4 grid gap-4 text-sm text-neutral-600 sm:grid-cols-4">
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
            className="small-caps bg-ink px-4 py-2 text-sm text-white transition hover:bg-ember"
          >
            open
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="small-caps border border-red-300/60 px-3 py-2 text-sm text-red-600 transition hover:border-red-300 hover:bg-red-50"
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
