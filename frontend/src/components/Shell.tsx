import { useCallback, useState, type ReactNode } from "react";

export type TabKey = "dashboard" | "schema";

type Tab = {
  key: TabKey;
  label: string;
  index: string;
};

const TABS: Tab[] = [
  { key: "dashboard", label: "dashboard", index: "01" },
  { key: "schema", label: "schema", index: "02" },
];

type Props = {
  sidebar: ReactNode;
  header: ReactNode;
  activeTab: TabKey;
  onTabChange: (tab: TabKey) => void;
  children: ReactNode;
  onDropFile?: (files: File[]) => void;
};

export function Shell({
  sidebar,
  header,
  activeTab,
  onTabChange,
  children,
  onDropFile,
}: Props) {
  const [chatWidth, setChatWidth] = useState(380);

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const fs = Array.from(e.dataTransfer.files ?? []);
    if (fs.length > 0 && onDropFile) onDropFile(fs);
  };
  const prevent = (e: React.DragEvent<HTMLDivElement>) => e.preventDefault();
  const startResize = useCallback(
    (event: React.PointerEvent<HTMLButtonElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = chatWidth;
      const onMove = (moveEvent: PointerEvent) => {
        const next = startWidth + moveEvent.clientX - startX;
        setChatWidth(Math.min(560, Math.max(300, next)));
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [chatWidth],
  );

  return (
    <div
      className="flex h-full w-full flex-col"
      onDragOver={prevent}
      onDragEnter={prevent}
      onDrop={handleDrop}
    >
      <div className="hairline flex min-h-14 items-stretch border-b bg-paper">
        <div className="min-w-0 flex-1">{header}</div>
        <div className="hairline flex shrink-0 items-end gap-5 border-l px-5">
          {TABS.map((tab) => {
            const active = tab.key === activeTab;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => onTabChange(tab.key)}
                className={`relative h-full px-1 text-xs ${
                  active ? "text-ink" : "text-neutral-500 hover:text-ink"
                }`}
              >
                <span className="small-caps">
                  {tab.index} {tab.label}
                </span>
                {active ? (
                  <span className="absolute inset-x-0 bottom-0 h-[2px] bg-ember" />
                ) : null}
              </button>
            );
          })}
        </div>
      </div>
      <div className="min-h-0 flex flex-1">
        <div className="min-w-[300px] max-w-[560px]" style={{ width: chatWidth }}>
          {sidebar}
        </div>
        <button
          type="button"
          aria-label="Resize chat and dashboard panes"
          onPointerDown={startResize}
          className="group hairline relative w-2 shrink-0 cursor-col-resize border-x bg-neutral-100 hover:bg-neutral-200"
        >
          <span className="absolute left-1/2 top-1/2 h-10 w-px -translate-x-1/2 -translate-y-1/2 bg-neutral-300 group-hover:bg-ember" />
        </button>
        <div className="min-w-[560px] flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}
