import { useEffect, useState } from "react";
import {
  discoverLinks,
  getFileSchema,
  reviewLink,
  skipReview,
} from "../lib/api";
import type {
  FileRecord,
  FileSchemaResponse,
  Link,
  LinkAction,
} from "../lib/types";
import { BarcodeTicks } from "./BarcodeTicks";
import { Modal } from "./Modal";

type Props = {
  sessionId: string;
  files: FileRecord[];
  links: Link[];
  onLinksChanged: () => void;
};

export function SchemaTab({ sessionId, files, links, onLinksChanged }: Props) {
  const [schemas, setSchemas] = useState<Record<string, FileSchemaResponse>>({});
  const [discovering, setDiscovering] = useState(false);
  const [showSkipModal, setShowSkipModal] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyLink, setBusyLink] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const missing = files.filter((f) => !schemas[f.id]);
    if (missing.length === 0) return;
    Promise.all(missing.map((f) => getFileSchema(f.id).catch(() => null)))
      .then((results) => {
        if (cancelled) return;
        setSchemas((prev) => {
          const next = { ...prev };
          results.forEach((r, i) => {
            const file = missing[i];
            if (r && file) next[file.id] = r;
          });
          return next;
        });
      });
    return () => {
      cancelled = true;
    };
  }, [files, schemas]);

  const fileById = (id: string) => files.find((f) => f.id === id);

  const handleDiscover = async () => {
    setDiscovering(true);
    setActionError(null);
    try {
      await discoverLinks(sessionId);
      onLinksChanged();
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setDiscovering(false);
    }
  };

  const handleReview = async (linkId: string, action: LinkAction) => {
    setBusyLink(linkId);
    setActionError(null);
    try {
      await reviewLink(linkId, action);
      onLinksChanged();
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusyLink(null);
    }
  };

  const handleSkipConfirmed = async () => {
    setShowSkipModal(false);
    try {
      await skipReview(sessionId);
      onLinksChanged();
    } catch (err) {
      setActionError((err as Error).message);
    }
  };

  return (
    <div className="px-8 py-6">
      <section className="mb-12">
        <header className="hairline border-b pb-2">
          <h2 className="small-caps text-sm text-ink">01 files</h2>
        </header>
        {files.length === 0 ? (
          <div className="mt-4 text-xs text-neutral-500">
            No files uploaded yet.
          </div>
        ) : (
          <div>
            {files.map((file) => {
              const sr = schemas[file.id];
              return (
                <div key={file.id} className="hairline border-b py-4">
                  <div className="flex items-baseline justify-between">
                    <div className="font-mono text-sm text-ink">
                      {file.filename}
                    </div>
                    <div className="font-mono text-xs text-neutral-500">
                      {file.row_count.toLocaleString()} rows
                    </div>
                  </div>
                  {sr ? (
                    <div className="mt-3 grid grid-cols-1 gap-x-8 gap-y-1 md:grid-cols-2">
                      {sr.schema.columns.map((c) => (
                        <div
                          key={`${c.name}-${c.position}`}
                          className="flex items-center justify-between gap-4 text-xs"
                        >
                          <span className="truncate font-mono text-ink">
                            {c.name}
                          </span>
                          <span className="flex items-center gap-2">
                            <span className="small-caps text-neutral-500">
                              {c.inferred_kind}
                            </span>
                            <BarcodeTicks value={c.confidence} />
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="mt-2 text-xs text-neutral-500">
                      loading schema{"\u2026"}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section>
        <header className="hairline flex items-center justify-between border-b pb-2">
          <h2 className="small-caps text-sm text-ink">02 links</h2>
          <div className="flex items-center gap-2">
            {links.length > 0 ? (
              <button
                type="button"
                onClick={() => setShowSkipModal(true)}
                className="small-caps border border-ink px-2 py-1 text-xs hover:bg-neutral-100"
              >
                skip review
              </button>
            ) : null}
            <button
              type="button"
              onClick={handleDiscover}
              disabled={discovering || files.length < 2}
              className="small-caps border border-ink px-2 py-1 text-xs hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {discovering ? "discovering\u2026" : "+ discover links"}
            </button>
          </div>
        </header>

        {actionError ? (
          <div className="mt-2 text-xs text-red-600">{actionError}</div>
        ) : null}

        {links.length === 0 ? (
          <div className="mt-4 text-xs text-neutral-500">
            No links yet. Discover links once at least two files are ingested.
          </div>
        ) : (
          <table className="mt-2 w-full border-collapse text-sm">
            <thead>
              <tr>
                <Th>from</Th>
                <Th>to</Th>
                <Th>direction</Th>
                <Th>summary</Th>
                <Th className="text-right">score</Th>
                <Th className="text-right">actions</Th>
              </tr>
            </thead>
            <tbody>
              {links.map((link) => {
                const fa = fileById(link.file_a);
                const fb = fileById(link.file_b);
                return (
                  <tr key={link.id} className="hairline border-b">
                    <Td mono>
                      {(fa?.filename ?? link.file_a) + "." + link.col_a}
                    </Td>
                    <Td mono>
                      {(fb?.filename ?? link.file_b) + "." + link.col_b}
                    </Td>
                    <Td>
                      <span className="small-caps text-xs text-neutral-500">
                        {link.direction.replace(/_/g, " ")}
                      </span>
                    </Td>
                    <Td>{link.summary ?? ""}</Td>
                    <Td mono className="text-right">
                      {link.score.toFixed(2)}
                    </Td>
                    <Td className="text-right">
                      <div className="flex justify-end gap-1">
                        <button
                          type="button"
                          disabled={busyLink === link.id}
                          onClick={() => handleReview(link.id, "confirm")}
                          className="small-caps border border-ink px-2 py-0.5 text-[11px] hover:bg-neutral-100 disabled:opacity-40"
                        >
                          confirm
                        </button>
                        <button
                          type="button"
                          disabled={busyLink === link.id}
                          onClick={() => handleReview(link.id, "reject")}
                          className="small-caps border border-ink px-2 py-0.5 text-[11px] hover:bg-neutral-100 disabled:opacity-40"
                        >
                          reject
                        </button>
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <Modal open={showSkipModal} onClose={() => setShowSkipModal(false)}>
        <h3 className="small-caps text-sm text-ink">skip link review?</h3>
        <p className="mt-3 text-sm text-ink">
          Cerno will treat every proposed link as confirmed. Please review the
          AI's understanding matches what you expect{"\u2014"}if any link is
          wrong, analysis can showcase incorrect results.
        </p>
        <p className="mt-3 text-sm text-neutral-600">
          You can still edit links later from the Schema tab.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setShowSkipModal(false)}
            className="small-caps border border-ink px-3 py-1 text-xs hover:bg-neutral-100"
          >
            cancel
          </button>
          <button
            type="button"
            onClick={handleSkipConfirmed}
            className="small-caps bg-ember px-3 py-1 text-xs text-white hover:bg-ember-hover"
          >
            skip anyway
          </button>
        </div>
      </Modal>
    </div>
  );
}

function Th({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <th
      className={`small-caps hairline border-b px-2 py-2 text-left text-[10px] text-neutral-500 ${className}`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  mono,
  className = "",
}: {
  children: React.ReactNode;
  mono?: boolean;
  className?: string;
}) {
  return (
    <td
      className={`px-2 py-2 align-top ${mono ? "font-mono tabular-nums text-xs" : "text-sm"} ${className}`}
    >
      {children}
    </td>
  );
}
