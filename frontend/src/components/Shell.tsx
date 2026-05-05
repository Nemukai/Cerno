import { type ReactNode } from "react";

export type TabKey = "ask" | "insights" | "files";

type Tab = {
  key: TabKey;
  label: string;
  index: string;
};

const TABS: Tab[] = [
  { key: "ask", label: "ask", index: "01" },
  { key: "insights", label: "insights", index: "02" },
  { key: "files", label: "files", index: "03" },
];

type Props = {
  sidebar: ReactNode;
  header: ReactNode;
  rightPanel?: ReactNode;
  activeTab: TabKey;
  onTabChange: (tab: TabKey) => void;
  children: ReactNode;
  onDropFile?: (files: File[]) => void;
  showTabs?: boolean;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
};

export function Shell({
  sidebar,
  header,
  rightPanel,
  activeTab,
  onTabChange,
  children,
  onDropFile,
  showTabs = true,
  sidebarOpen,
  onToggleSidebar,
}: Props) {
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const fs = Array.from(e.dataTransfer.files ?? []);
    if (fs.length > 0 && onDropFile) onDropFile(fs);
  };
  const prevent = (e: React.DragEvent<HTMLDivElement>) => e.preventDefault();

  return (
    <div
      className="flex h-full w-full flex-col"
      onDragOver={prevent}
      onDragEnter={prevent}
      onDrop={handleDrop}
    >
      <div className="hairline flex min-h-14 items-stretch border-b bg-paper">
        <div className="min-w-0 flex-1">{header}</div>
      </div>
      <div className="min-h-0 flex flex-1">
        {sidebarOpen ? (
          <div className="hairline relative w-[270px] shrink-0 border-r bg-[#fbfaf8]">
            {sidebar}
            <button
              type="button"
              onClick={onToggleSidebar}
              className="small-caps absolute bottom-4 left-5 right-5 border border-neutral-300 bg-white px-3 py-2 text-xs text-neutral-500 hover:border-ember hover:text-ember"
            >
              hide sidebar
            </button>
          </div>
        ) : (
          <div className="relative w-0 shrink-0">
            <button
              type="button"
              onClick={onToggleSidebar}
              className="small-caps absolute bottom-4 left-4 z-20 w-32 border border-neutral-300 bg-white px-3 py-2 text-xs text-neutral-500 shadow-[0_10px_30px_rgba(14,14,14,0.08)] hover:border-ember hover:text-ember"
              title="Show sidebar"
            >
              show sidebar
            </button>
          </div>
        )}
        <div className="min-w-[520px] flex-1 bg-[#fffdf9]">
          {showTabs ? (
            <div className="hairline flex h-12 items-end gap-6 border-b bg-paper px-8">
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
          ) : null}
          <main className={showTabs ? "h-[calc(100%-3rem)] overflow-y-auto" : "h-full overflow-y-auto"}>
            {children}
          </main>
        </div>
        {rightPanel ? (
          <aside className="hairline w-[390px] shrink-0 overflow-y-auto border-l bg-white">
            {rightPanel}
          </aside>
        ) : null}
      </div>
    </div>
  );
}
