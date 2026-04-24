import { useState } from "react";
import type { ChatTurn, DashboardPage } from "../lib/types";
import { ViewChip } from "./ViewChip";

type Props = {
  turns: ChatTurn[];
  pages: DashboardPage[];
  disabled: boolean;
  sending: boolean;
  onSend: (message: string) => void;
  onOpenPage: (pageId: string) => void;
};

export function ChatSidebar({
  turns,
  pages,
  disabled,
  sending,
  onSend,
  onOpenPage,
}: Props) {
  const [input, setInput] = useState("");

  const submit = () => {
    const msg = input.trim();
    if (!msg || disabled || sending) return;
    onSend(msg);
    setInput("");
  };

  const pageByIdTitle = (id: string): string => {
    const p = pages.find((x) => x.id === id);
    if (!p) return "VIEW";
    const num = String(p.position + 1).padStart(2, "0");
    return `${num} ${p.title.toUpperCase()}`;
  };

  return (
    <aside className="hairline flex h-full w-[380px] shrink-0 flex-col border-r bg-paper">
      <div className="hairline border-b px-4 py-3">
        <div className="small-caps text-xs text-neutral-500">chat</div>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-2">
        {turns.length === 0 ? (
          <div className="mt-6 text-xs text-neutral-500">
            Ask Cerno a question about your data.
          </div>
        ) : (
          turns.map((turn) => (
            <div key={turn.id} className="hairline border-b py-4">
              <div className="small-caps mb-1 text-[10px] text-neutral-500">
                user
              </div>
              <div className="mb-4 whitespace-pre-wrap text-sm text-ink">
                {turn.user_message}
              </div>
              <div className="small-caps mb-1 text-[10px] text-ember">
                cerno
              </div>
              <div className="whitespace-pre-wrap text-sm text-ink">
                {turn.assistant_message ??
                  (turn.state === "failed" ? "(failed)" : "\u2026")}
              </div>
              {turn.spawned_page_id ? (
                <div className="mt-3">
                  <ViewChip
                    label={pageByIdTitle(turn.spawned_page_id)}
                    onClick={() => onOpenPage(turn.spawned_page_id!)}
                  />
                </div>
              ) : null}
            </div>
          ))
        )}
      </div>
      <div className="hairline border-t px-4 py-3">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={disabled ? "upload a file to start" : "ask\u2026"}
          disabled={disabled}
          rows={3}
          className="hairline w-full resize-none border bg-white p-2 font-sans text-sm text-ink focus:outline-none focus:ring-1 focus:ring-ember disabled:opacity-40"
        />
        <div className="mt-2 flex items-center justify-between">
          <div className="text-[10px] text-neutral-400">{"\u2318 + enter"}</div>
          <button
            type="button"
            onClick={submit}
            disabled={disabled || sending || !input.trim()}
            className="small-caps bg-ember px-3 py-1 text-xs text-white hover:bg-ember-hover disabled:cursor-not-allowed disabled:opacity-40"
          >
            {sending ? "sending\u2026" : "send"}
          </button>
        </div>
      </div>
    </aside>
  );
}
