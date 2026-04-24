import type { ReactNode } from "react";

export type TabKey = "dashboard" | "notebook" | "schema";

type Tab = {
  key: TabKey;
  label: string;
  index: string;
};

const TABS: Tab[] = [
  { key: "dashboard", label: "dashboard", index: "01" },
  { key: "notebook", label: "notebook", index: "02" },
  { key: "schema", label: "schema", index: "03" },
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
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const fs = Array.from(e.dataTransfer.files ?? []);
    if (fs.length > 0 && onDropFile) onDropFile(fs);
  };
  const prevent = (e: React.DragEvent<HTMLDivElement>) => e.preventDefault();

  return (
    <div className="flex h-full w-full">
      {sidebar}
      <div
        className="flex min-w-0 flex-1 flex-col"
        onDragOver={prevent}
        onDragEnter={prevent}
        onDrop={handleDrop}
      >
        {header}
        <div className="hairline flex items-end gap-6 border-b px-6">
          {TABS.map((tab) => {
            const active = tab.key === activeTab;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => onTabChange(tab.key)}
                className={`relative py-3 text-xs ${active ? "text-ink" : "text-neutral-500 hover:text-ink"}`}
              >
                <span className="small-caps">
                  {tab.index} {tab.label}
                </span>
                {active ? (
                  <span className="absolute inset-x-0 -bottom-px h-[2px] bg-ember" />
                ) : null}
              </button>
            );
          })}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}
