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
import { DashboardTab } from "./components/DashboardTab";
import { SchemaTab } from "./components/SchemaTab";
import { SessionHeader } from "./components/SessionHeader";
import { Shell, type TabKey } from "./components/Shell";

export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [homeView, setHomeView] = useState<"landing" | "sessions">("landing");
  const [session, setSession] = useState<Session | null>(null);
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
      <SessionsDashboard
        sessions={sessions}
        onStart={handleCreateSession}
        onResume={handleResumeSession}
        onDelete={handleDeleteSession}
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
          <div className="font-mono text-sm text-ink">Cerno</div>
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
            <h1 className="mt-4 max-w-4xl font-mono text-5xl leading-[1.02] text-ink sm:text-7xl lg:text-8xl">
              Cerno
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
                {sessionCount} saved session{sessionCount === 1 ? "" : "s"}
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
            body="Approved sessions open into generated views that can be reshaped for the task."
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

function SessionsDashboard({
  sessions,
  onStart,
  onResume,
  onDelete,
  starting,
  error,
  onDismissError,
  onBackToLanding,
}: {
  sessions: Session[];
  onStart: (name: string) => void;
  onResume: (session: Session) => void;
  onDelete: (id: string) => void;
  starting: boolean;
  error: string | null;
  onDismissError: () => void;
  onBackToLanding: () => void;
}) {
  const [name, setName] = useState("");
  const [pointer, setPointer] = useState({ x: 0.5, y: 0.5 });
  const stats = buildSessionStats(sessions);

  const handleStart = () => {
    const trimmed = name.trim();
    const fallback = `session ${new Date().toLocaleDateString()}`;
    onStart(trimmed.length > 0 ? trimmed : fallback);
  };

  const handleKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !starting) handleStart();
  };

  const handleDelete = (s: Session) => {
    const ok = window.confirm(
      `Delete session "${s.name}"? Files, links, dashboards, and chat history will be removed. This cannot be undone.`,
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

  const asciiRows = [
    "CERNO::LINK_GRAPH   +--+--+--+   DISCOVERY_PIPELINE",
    "      .--.     rows -> joins -> signals      .--.",
    "  +---|  |---+  [hash] [schema] [rank]  +---|  |---+",
    "      '--'     outliers / keys / lineage      '--'",
    ">>> ingest.csv :::::::::::: correlate.xlsx :::::::::",
    "     SELECT * FROM memory WHERE signal > noise",
    "  01001011 01000101 01011001 00101101 01010011",
    "schema_map -> relationship_graph -> dashboard_queue",
  ];

  return (
    <div
      className="relative flex min-h-full w-full overflow-hidden bg-paper px-6 py-10 text-ink"
      onMouseMove={handlePointerMove}
    >
      <div className="pointer-events-none absolute inset-0 opacity-90">
        <div
          className="absolute inset-y-0 w-32 bg-ember/10 blur-2xl transition-transform duration-200 ease-out"
          style={{
            left: `${pointer.x * 100}%`,
            transform: "translateX(-50%) skewX(-12deg)",
          }}
        />
        <div
          className="absolute inset-0 bg-[linear-gradient(115deg,transparent_0%,rgba(232,93,35,0.10)_var(--scan),transparent_calc(var(--scan)_+_18%))]"
          style={
            {
              "--scan": `${pointer.x * 100}%`,
            } as React.CSSProperties
          }
        />
        <div className="absolute inset-0 flex select-none flex-col justify-around py-8 font-mono text-[10px] uppercase leading-loose text-ember/25 sm:text-xs lg:text-sm">
          {Array.from({ length: 18 }).map((_, i) => {
            const direction = i % 2 === 0 ? 1 : -1;
            const driftX = (pointer.x - 0.5) * direction * (18 + (i % 5) * 4);
            const driftY = (pointer.y - 0.5) * direction * 10;
            return (
              <div
                key={i}
                className="whitespace-nowrap transition-transform duration-200 ease-out"
                style={{
                  transform: `translate3d(${driftX}px, ${driftY}px, 0)`,
                  opacity: 0.16 + (i % 4) * 0.06,
                }}
              >
                {asciiRows[i % asciiRows.length]}{" "}
                {asciiRows[(i + 3) % asciiRows.length]}
              </div>
            );
          })}
        </div>
      </div>

      <main className="relative z-10 mx-auto flex w-full max-w-6xl flex-col py-4">
        <header className="flex items-center justify-between gap-4">
          <button
            type="button"
            onClick={onBackToLanding}
            className="small-caps text-xs text-neutral-500 hover:text-ember"
          >
            cerno
          </button>
          <div className="small-caps text-xs text-neutral-500">workspace dashboard</div>
        </header>

        <section className="mt-14 grid gap-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(22rem,0.9fr)]">
          <div>
            <div className="small-caps text-xs text-ember">sessions</div>
            <h1 className="mt-2 max-w-3xl font-mono text-4xl tracking-tight text-ink sm:text-5xl">
              Choose the workspace, then work the data.
            </h1>
            <p className="mt-4 max-w-2xl text-sm leading-6 text-neutral-600">
              Create a session for a new upload or resume a saved one. Sessions carry
              files, the approved schema map, generated guidance, dashboard views,
              and chat history together.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-x-8 gap-y-5 border-y border-ink/10 py-5">
            <SessionStat label="total" value={stats.total} />
            <SessionStat label="ready" value={stats.ready} />
            <SessionStat label="in review" value={stats.review} />
            <SessionStat label="latest" value={stats.latestLabel} />
          </div>
        </section>

        <section className="mx-auto mt-12 w-full max-w-4xl">
          <div className="mt-8 w-full border border-ember/50 bg-white/85 p-1 shadow-[0_18px_70px_rgba(232,93,35,0.16)] backdrop-blur">
            <div className="flex flex-col gap-1 sm:flex-row">
              <label className="flex min-w-0 flex-1 items-center gap-3 bg-paper px-4 py-3 text-left text-ink">
                <span className="small-caps shrink-0 text-[11px] text-neutral-500">
                  session
                </span>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={handleKey}
                  placeholder="march billings"
                  className="min-w-0 flex-1 bg-transparent font-mono text-sm text-ink placeholder:text-neutral-400 focus:outline-none"
                />
              </label>
              <button
                type="button"
                onClick={handleStart}
                disabled={starting}
                className="small-caps bg-ember px-5 py-3 text-xs text-white transition hover:bg-ember-hover disabled:opacity-40"
              >
                {starting ? "starting..." : "start session"}
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

        <section className="mx-auto mt-12 w-full max-w-4xl">
          <div className="flex items-end justify-between gap-4 border-b border-ink/10 pb-3">
            <div>
              <div className="small-caps text-xs text-ember/80">
                saved workspaces
              </div>
              <h2 className="mt-1 font-mono text-xl text-ink">
                Pick up where the data left off
              </h2>
            </div>
            <span className="font-mono text-xs text-neutral-500">
              {sessions.length} saved
            </span>
          </div>

          {sessions.length > 0 ? (
            <ul className="mt-4 grid gap-3">
              {sessions.map((s) => (
                <li key={s.id}>
                  <div className="group flex items-center gap-4 border border-ink/10 bg-white/80 px-4 py-3 shadow-[0_10px_35px_rgba(14,14,14,0.04)] transition hover:border-ember/70 hover:bg-ember/[0.08]">
                    <button
                      type="button"
                      onClick={() => onResume(s)}
                      className="grid min-w-0 flex-1 grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-1 text-left"
                    >
                      <span className="row-span-2 h-8 w-1 bg-ember transition group-hover:h-10" />
                      <span className="truncate font-mono text-sm text-ink">
                        {s.name}
                      </span>
                      <span className="small-caps text-[10px] text-neutral-500">
                        {s.status} / {new Date(s.created_at).toLocaleString()}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(s)}
                      className="small-caps border border-red-300/50 px-2 py-1 text-[11px] text-red-600 transition hover:border-red-300 hover:bg-red-50"
                    >
                      delete
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="mt-4 border border-dashed border-ink/15 bg-white/50 px-4 py-6 font-mono text-sm text-neutral-500">
              No saved sessions yet.
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

function SessionStat({
  label,
  value,
}: {
  label: string;
  value: number | string;
}) {
  return (
    <div>
      <div className="small-caps text-[11px] text-neutral-500">{label}</div>
      <div className="mt-1 font-mono text-2xl text-ink">{value}</div>
    </div>
  );
}

function buildSessionStats(sessions: Session[]) {
  const ready = sessions.filter((session) => session.status === "ready").length;
  const review = sessions.filter(
    (session) => session.discovery_status === "pending_review",
  ).length;
  const latest = sessions
    .map((session) => new Date(session.created_at))
    .filter((date) => !Number.isNaN(date.getTime()))
    .sort((a, b) => b.getTime() - a.getTime())[0];
  return {
    total: sessions.length,
    ready,
    review,
    latestLabel: latest ? latest.toLocaleDateString() : "none",
  };
}
