import { FileText, Map, MessageSquare, TableProperties } from "lucide-react";

import type { MainTabId } from "../types";

interface Props {
  active: MainTabId;
  hasSpatial: boolean;
  onChange: (tab: MainTabId) => void;
}

export function MainTabs({ active, hasSpatial, onChange }: Props) {
  const tabs = [
    { id: "chat" as const, label: "对话", icon: <MessageSquare size={14} /> },
    { id: "data" as const, label: "数据表", icon: <TableProperties size={14} /> },
    { id: "policy" as const, label: "政策文件", icon: <FileText size={14} /> },
    ...(hasSpatial ? [{ id: "map" as const, label: "地图", icon: <Map size={14} /> }] : []),
  ];

  return (
    <nav className="main-tabs" role="tablist" aria-label="主页面">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={active === tab.id}
          className={`main-tab ${active === tab.id ? "active" : ""}`}
          onClick={() => onChange(tab.id)}
        >
          {tab.icon}{tab.label}
        </button>
      ))}
    </nav>
  );
}
