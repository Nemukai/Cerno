import { useCallback, useEffect, useRef, useState } from "react";
import {
  buildDashboard,
  createSession,
  getDashboard,
  listFiles,
  listLinks,
  listSessions,
  listTurns,
  postChat,
  uploadFile,
} from "./lib/api";
import type {
  ChatTurn,
  DashboardPage,
  FileRecord,
  Link,
  NotebookCell,
  Session,
} from "./lib/types";
import { ChatSidebar } from "./components/ChatSidebar";
import { DashboardTab } from "./components/DashboardTab";
import { NotebookTab } from "./components/NotebookTab";
import { SchemaTab } from "./components/SchemaTab";
import { SessionHeader } from "./components/SessionHeader";
import { Shell, type TabKey } from "./components/Shell";

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [pages, setPages] = useState<DashboardPage[]>([]);
  const [cellsByPage, setCellsByPage] = useState<Record<string, NotebookCell[]>>(
    {},
  );
  const [activeTab, setActiveTab] = useState<TabKey>("dashboard");
  const [focusPageId, setFocusPageId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bootstrapped = useRef(false);

  const refreshFiles = useCallback(async (sessionId: string) => {
    const fs = await listFiles(sessionId);
    setFiles(fs);
  }, []);

  const refreshLinks = useCallback(async (sessionId: string) => {
    const ls = await listLinks(sessionId);
    setLinks(ls);
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
    listSessions()
      .then((sessions) => {
        if (sessions.length === 0) return;
        const first = sessions[0];
        if (!first) return;
        setSession(first);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!session) return;
    const id = session.id;
    Promise.all([
      refreshFiles(id),
      refreshLinks(id),
      refreshDashboard(id),
      refreshTurns(id),
    ]).catch((err: Error) => setError(err.message));
  }, [session, refreshFiles, refreshLinks, refreshDashboard, refreshTurns]);

  const ensureSession = useCallback(
    async (name: string): Promise<Session> => {
      if (session) return session;
      const created = await createSession(name);
      setSession(created);
      return created;
    },
    [session],
  );

  const handleUpload = useCallback(
    async (file: File) => {
      setError(null);
      setUploading(true);
      try {
        const defaultName = file.name.replace(/\.[^.]+$/, "");
        const s = await ensureSession(defaultName);
        await uploadFile(s.id, file, file.name);
        await refreshFiles(s.id);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setUploading(false);
      }
    },
    [ensureSession, refreshFiles],
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

  const handleLinksChanged = useCallback(() => {
    if (!session) return;
    refreshLinks(session.id).catch((err: Error) => setError(err.message));
  }, [session, refreshLinks]);

  if (!session && files.length === 0) {
    return <FirstRun onUpload={handleUpload} uploading={uploading} error={error} />;
  }

  return (
    <>
      <Shell
        sidebar={
          <ChatSidebar
            turns={turns}
            pages={pages}
            disabled={!session || files.length === 0}
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
            onBuildDashboard={handleBuildDashboard}
            building={building}
            hasFiles={files.length > 0}
          />
        ) : null}
        {activeTab === "notebook" ? (
          <NotebookTab
            pages={pages}
            cellsByPage={cellsByPage}
            focusPageId={focusPageId}
          />
        ) : null}
        {activeTab === "schema" && session ? (
          <SchemaTab
            sessionId={session.id}
            files={files}
            links={links}
            onLinksChanged={handleLinksChanged}
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

function FirstRun({
  onUpload,
  uploading,
  error,
}: {
  onUpload: (file: File) => void;
  uploading: boolean;
  error: string | null;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handlePick = () => inputRef.current?.click();
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) onUpload(f);
    e.target.value = "";
  };
  const prevent = (e: React.DragEvent<HTMLDivElement>) => e.preventDefault();
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const f = e.dataTransfer.files?.[0];
    if (f) onUpload(f);
  };

  return (
    <div
      className="flex h-full w-full items-center justify-center p-10"
      onDragOver={prevent}
      onDragEnter={prevent}
      onDrop={handleDrop}
    >
      <div className="max-w-md">
        <div className="small-caps text-xs text-neutral-500">cerno</div>
        <h1 className="mt-1 font-mono text-3xl tracking-tight text-ink">
          discern what matters
        </h1>
        <p className="mt-4 text-sm text-neutral-600">
          Drop a CSV or XLSX file to start a session. Cerno ingests the file,
          discovers links between tables, and builds you a dashboard.
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,.xls"
          className="hidden"
          onChange={handleChange}
        />
        <div className="mt-6 flex gap-2">
          <button
            type="button"
            onClick={handlePick}
            disabled={uploading}
            className="small-caps border border-ink bg-ember px-3 py-2 text-xs text-white hover:bg-ember-hover disabled:opacity-40"
          >
            {uploading ? "uploading\u2026" : "+ new session"}
          </button>
        </div>
        {error ? (
          <div className="mt-4 font-mono text-xs text-red-600">{error}</div>
        ) : null}
        <div className="mt-8 border border-dashed border-neutral-300 p-10 text-center text-xs text-neutral-500">
          or drop a file anywhere in this window
        </div>
      </div>
    </div>
  );
}
