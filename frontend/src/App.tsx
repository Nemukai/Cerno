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
  DashboardPage,
  DiscoveredFile,
  DiscoveredLink,
  DiscoveryResponse,
  FileRecord,
  Link,
  NotebookCell,
  ProcessingEvent,
  Session,
} from "./lib/types";
import { ChatSidebar } from "./components/ChatSidebar";
import { DashboardTab } from "./components/DashboardTab";
import { NotebookTab } from "./components/NotebookTab";
import { SchemaTab } from "./components/SchemaTab";
import { SessionHeader } from "./components/SessionHeader";
import { Shell, type TabKey } from "./components/Shell";

export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [discovery, setDiscovery] = useState<DiscoveryResponse | null>(null);
  const [events, setEvents] = useState<ProcessingEvent[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [pages, setPages] = useState<DashboardPage[]>([]);
  const [cellsByPage, setCellsByPage] = useState<Record<string, NotebookCell[]>>(
    {},
  );
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
  }, []);

  const refreshTurns = useCallback(async (sessionId: string) => {
    const ts = await listTurns(sessionId);
    setTurns(ts);
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
    ]).catch((err: Error) => setError(err.message));
  }, [
    session,
    refreshFiles,
    refreshLinks,
    refreshDiscovery,
    refreshEvents,
    refreshDashboard,
    refreshTurns,
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
    setPages([]);
    setCellsByPage({});
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
      setSession(s);
    },
    [clearSessionState],
  );

  const handleHome = useCallback(() => {
    setSession(null);
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
    try {
      const result = await processSession(session.id);
      setDiscovery(result);
      await Promise.all([
        refreshFiles(session.id),
        refreshLinks(session.id),
        refreshEvents(session.id),
      ]);
    } catch (err) {
      setError((err as Error).message);
      await refreshDiscovery(session.id).catch(() => undefined);
      await refreshEvents(session.id).catch(() => undefined);
    } finally {
      setProcessing(false);
    }
  }, [session, refreshFiles, refreshLinks, refreshDiscovery, refreshEvents]);

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
          refreshSessions(),
        ]);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setApproving(false);
      }
    },
    [session, refreshFiles, refreshLinks, refreshSessions],
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

  if (!session) {
    return (
      <SessionStart
        sessions={sessions}
        onStart={handleCreateSession}
        onResume={handleResumeSession}
        onDelete={handleDeleteSession}
        starting={starting}
        error={error}
        onDismissError={() => setError(null)}
      />
    );
  }

  const discoveryStatus = discovery?.status ?? "empty";
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
            onUpload={handleUpload}
            uploading={uploading}
            onHome={handleHome}
            onDelete={() => handleDeleteSession(session.id)}
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
        {activeTab === "notebook" ? (
          <NotebookTab
            pages={pages}
            cellsByPage={cellsByPage}
            focusPageId={focusPageId}
          />
        ) : null}
        {activeTab === "schema" ? (
          <SchemaTab
            files={files}
            links={links}
            discovery={discovery}
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

function SessionStart({
  sessions,
  onStart,
  onResume,
  onDelete,
  starting,
  error,
  onDismissError,
}: {
  sessions: Session[];
  onStart: (name: string) => void;
  onResume: (session: Session) => void;
  onDelete: (id: string) => void;
  starting: boolean;
  error: string | null;
  onDismissError: () => void;
}) {
  const [name, setName] = useState("");

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

  return (
    <div className="flex h-full w-full items-center justify-center p-10">
      <div className="w-full max-w-xl">
        <div className="small-caps text-xs text-neutral-500">cerno</div>
        <h1 className="mt-1 font-mono text-3xl tracking-tight text-ink">
          discern what matters
        </h1>
        <p className="mt-4 text-sm text-neutral-600">
          Start a new session to ingest files, discover links between them, and
          build a dashboard.
        </p>
        <label className="mt-6 block">
          <span className="small-caps text-xs text-neutral-500">
            session name
          </span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={handleKey}
            placeholder="e.g. march billings"
            className="mt-2 block w-full border border-ink px-3 py-2 font-mono text-sm focus:outline-none"
          />
        </label>
        <div className="mt-5">
          <button
            type="button"
            onClick={handleStart}
            disabled={starting}
            className="small-caps border border-ink bg-ember px-3 py-2 text-xs text-white hover:bg-ember-hover disabled:opacity-40"
          >
            {starting ? "starting\u2026" : "+ start session"}
          </button>
        </div>
        {error ? (
          <div className="mt-4 flex items-start justify-between gap-3 border border-red-300 px-3 py-2 font-mono text-xs text-red-600">
            <span>{error}</span>
            <button
              type="button"
              onClick={onDismissError}
              className="text-neutral-500 hover:text-ink"
            >
              {"\u00d7"}
            </button>
          </div>
        ) : null}

        {sessions.length > 0 ? (
          <section className="mt-10">
            <div className="small-caps text-xs text-neutral-500">
              resume a session
            </div>
            <ul className="mt-3">
              {sessions.map((s) => (
                <li
                  key={s.id}
                  className="hairline flex items-center justify-between gap-4 border-b py-3"
                >
                  <button
                    type="button"
                    onClick={() => onResume(s)}
                    className="flex min-w-0 flex-1 flex-col items-start text-left hover:text-ember"
                  >
                    <span className="truncate font-mono text-sm text-ink">
                      {s.name}
                    </span>
                    <span className="small-caps text-[10px] text-neutral-500">
                      {s.status} {"\u00b7"}{" "}
                      {new Date(s.created_at).toLocaleString()}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(s)}
                    className="small-caps border border-red-600 px-2 py-1 text-[11px] text-red-600 hover:bg-red-50"
                  >
                    delete
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </div>
  );
}
