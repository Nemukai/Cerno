import { type ReactNode } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

export type TabKey = "ask" | "insights";

type Tab = {
  key: TabKey;
  label: string;
  index: string;
};

const TABS: Tab[] = [
  { key: "ask", label: "ask", index: "01" },
  { key: "insights", label: "insights", index: "02" },
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
      className="cerno-void flex h-full w-full flex-col"
      onDragOver={prevent}
      onDragEnter={prevent}
      onDrop={handleDrop}
    >
      <div className="border-border flex min-h-14 items-stretch border-b bg-card">
        <div className="min-w-0 flex-1">{header}</div>
      </div>
      <div className="min-h-0 flex flex-1">
        {sidebarOpen ? (
          <div className="border-border flex w-[270px] shrink-0 flex-col border-r bg-muted/30">
            <div className="min-h-0 flex-1">{sidebar}</div>
            <div className="flex justify-end border-t border-border/70 p-3">
              <button
                type="button"
                onClick={onToggleSidebar}
                className="grid size-9 place-items-center border border-border bg-card text-muted-foreground transition hover:border-primary hover:text-primary"
                aria-label="Hide sidebar"
                title="Hide sidebar"
              >
                <PanelLeftClose size={17} strokeWidth={1.8} />
                <span className="sr-only">Hide sidebar</span>
              </button>
            </div>
          </div>
        ) : (
          <div className="border-border flex w-12 shrink-0 flex-col justify-end border-r bg-muted/30 p-1.5">
            <button
              type="button"
              onClick={onToggleSidebar}
              className="grid size-9 place-items-center border border-border bg-card text-muted-foreground transition hover:border-primary hover:text-primary"
              aria-label="Show sidebar"
              title="Show sidebar"
            >
              <PanelLeftOpen size={17} strokeWidth={1.8} />
              <span className="sr-only">Show sidebar</span>
            </button>
          </div>
        )}
        <div className="min-w-[520px] flex-1 bg-background">
          {showTabs ? (
            <div className="border-border flex h-12 items-end gap-6 border-b bg-card px-8">
              {TABS.map((tab) => {
                const active = tab.key === activeTab;
                return (
                  <button
                    key={tab.key}
                    type="button"
                    onClick={() => onTabChange(tab.key)}
                    className={`relative h-full px-1 text-xs ${
                      active ? "text-foreground" : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <span className="small-caps">
                      {tab.index} {tab.label}
                    </span>
                    {active ? (
                      <span className="absolute inset-x-0 bottom-0 h-[2px] bg-primary" />
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
          <aside className="border-border w-[390px] shrink-0 overflow-y-auto border-l bg-card">
            {rightPanel}
          </aside>
        ) : null}
      </div>
    </div>
  );
}
