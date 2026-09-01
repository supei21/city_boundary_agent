import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import type { FeatureCollection } from "geojson";

import {
  generatePolicyReport,
  getArtifact,
  getWorkspace,
  resetWorkspace,
  streamChat,
  uploadConstraintDataset,
  uploadDataset,
} from "./api";
import { AddModelModal } from "./components/AddModelModal";
import { AnalysisSidebar } from "./components/AnalysisSidebar";
import { ChatPanel } from "./components/ChatPanel";
import { DataTableView } from "./components/DataTableView";
import { LeftSidebar } from "./components/LeftSidebar";
import { MainTabs } from "./components/MainTabs";
import { MapView } from "./components/MapView";
import { PolicyDocumentView } from "./components/PolicyDocumentView";
import { extractSpatial } from "./previewUtils";
import type {
  ArtifactDetail,
  ArtifactSummary,
  ChatMessage,
  Decision,
  MainTabId,
  StreamPacket,
  WorkspaceSummary,
} from "./types";

const POLICY_REPORT_PROMPT = "请根据当前候选地块分析结果和本地政策参考文档，生成《LLM驱动的城镇开发边界调整政策》PDF，内容重点包括调整说明、政策依据和优化策略，并在生成后进行预览。";

const WELCOME_MESSAGE: ChatMessage = {
  id: "welcome",
  role: "assistant",
  content: "您好，我是城镇开发边界识别智能体。我可以基于街区属性数据，调用 XGBoost、SHAP 与空间约束规则，对地块开发潜力进行评估与解释，并辅助识别调入、调出及重点复核候选地块，生成可视化结果与决策依据。\n\n请上传或选择研究区数据，并告诉我您希望开展的分析任务。",
};

export default function App() {
  const [workspace, setWorkspace] = useState<WorkspaceSummary | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME_MESSAGE]);
  const [busy, setBusy] = useState(false);
  const [selectedModelId, setSelectedModelId] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [addModelOpen, setAddModelOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(true);
  const [leftWidth, setLeftWidth] = useState(() => Number(localStorage.getItem("boundary-left-width")) || 268);
  const [rightWidth, setRightWidth] = useState(() => Number(localStorage.getItem("boundary-right-width")) || 330);
  const [resizing, setResizing] = useState<"left" | "right" | null>(null);
  const [selectedArtifact, setSelectedArtifact] = useState<ArtifactSummary | null>(null);
  const [artifactDetail, setArtifactDetail] = useState<ArtifactDetail | null>(null);
  const [activeTab, setActiveTab] = useState<MainTabId>("chat");
  const [mapData, setMapData] = useState<FeatureCollection | null>(null);
  const [lastDecisions, setLastDecisions] = useState<Decision[]>([]);
  const [dataVersion, setDataVersion] = useState(0);

  const latestPolicyReport = workspace?.artifacts.find((item) => (
    item.kind === "report" && item.mime === "application/pdf"
  )) ?? null;

  const refreshWorkspace = useCallback(async () => {
    const next = await getWorkspace();
    setWorkspace(next);
    setSelectedModelId((current) => (
      next.models.some((model) => model.id === current && model.ready)
        ? current
        : next.default_model_id
    ));
    return next;
  }, []);

  useEffect(() => {
    refreshWorkspace().catch((error: Error) => {
      setMessages([{ ...WELCOME_MESSAGE, content: `无法连接后端服务：${error.message}`, error: true }]);
    });
  }, [refreshWorkspace]);

  useEffect(() => {
    if (!selectedArtifact) {
      setArtifactDetail(null);
      return;
    }
    setArtifactDetail(null);
    getArtifact(selectedArtifact.id)
      .then((detail) => {
        setArtifactDetail(detail);
        const spatial = extractSpatial(detail.preview);
        if (spatial) setMapData(spatial);
      })
      .catch(() => setArtifactDetail(null));
  }, [selectedArtifact]);

  useEffect(() => {
    if (!workspace?.has_spatial_data) {
      setMapData(null);
      setActiveTab((tab) => (tab === "map" ? "chat" : tab));
    }
  }, [workspace?.has_spatial_data]);

  useEffect(() => {
    if (!resizing) return;

    const onMouseMove = (event: MouseEvent) => {
      if (resizing === "left" && !sidebarCollapsed) {
        const next = Math.min(Math.max(event.clientX, 220), 420);
        setLeftWidth(next);
        localStorage.setItem("boundary-left-width", String(next));
      }
      if (resizing === "right" && previewOpen) {
        const next = Math.min(Math.max(window.innerWidth - event.clientX, 300), 420);
        setRightWidth(next);
        localStorage.setItem("boundary-right-width", String(next));
      }
    };

    const stop = () => setResizing(null);
    document.body.classList.add("is-resizing-panels");
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", stop);
    return () => {
      document.body.classList.remove("is-resizing-panels");
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", stop);
    };
  }, [previewOpen, resizing, sidebarCollapsed]);

  const selectArtifact = (artifact: ArtifactSummary) => {
    setSelectedArtifact(artifact);
  };

  const updateAssistant = (id: string, update: (message: ChatMessage) => ChatMessage) => {
    setMessages((current) => current.map((message) => message.id === id ? update(message) : message));
  };

  const handleSend = async (prompt: string, modelId: string = selectedModelId) => {
    if (busy || !workspace?.block_count) return;
    const activeModelId = modelId || workspace.default_model_id;
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: "user", content: prompt };
    const assistantId = crypto.randomUUID();
    const assistantMessage: ChatMessage = { id: assistantId, role: "assistant", content: "", events: [], pending: true };
    setMessages((current) => [...current, userMessage, assistantMessage]);
    setBusy(true);

    try {
      await streamChat(prompt, activeModelId, (packet: StreamPacket) => {
        if (packet.type === "process") {
          updateAssistant(assistantId, (message) => ({
            ...message,
            events: [
              ...(message.events ?? []).map((event) => event.status === "running" ? { ...event, status: "completed" as const } : event),
              packet.event,
            ],
          }));
        } else if (packet.type === "answer_delta") {
          updateAssistant(assistantId, (message) => ({
            ...message,
            content: `${message.content}${packet.content}`,
          }));
        } else if (packet.type === "result") {
          updateAssistant(assistantId, (message) => ({
            ...message,
            content: packet.result.response,
            result: packet.result,
            pending: false,
            events: (message.events ?? []).map((event) => event.status === "running" ? { ...event, status: "completed" as const } : event),
          }));
          setLastDecisions(packet.result.decisions ?? []);
          if (packet.artifact) selectArtifact(packet.artifact);
        } else {
          updateAssistant(assistantId, (message) => ({ ...message, content: packet.message, pending: false, error: true }));
        }
      });
      await refreshWorkspace();
    } catch (error) {
      const message = error instanceof Error ? error.message : "未知错误";
      updateAssistant(assistantId, (item) => ({ ...item, content: `分析请求失败：${message}`, pending: false, error: true }));
    } finally {
      setBusy(false);
    }
  };

  const handleUpload = async (file: File) => {
    setBusy(true);
    try {
      const next = await uploadDataset(file);
      setWorkspace(next);
      setDataVersion((version) => version + 1);
      const uploaded = next.artifacts[0];
      if (uploaded) selectArtifact(uploaded);
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: [
            `已加载 **${file.name}**，共识别 ${next.block_count} 个街区、${next.field_count} 个字段。`,
            ...next.dataset_warnings.map((warning) => `- ${warning}`),
          ].join("\n"),
        },
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "未知错误";
      setMessages((current) => [...current, { id: crypto.randomUUID(), role: "assistant", content: `文件上传失败：${message}`, error: true }]);
    } finally {
      setBusy(false);
    }
  };

  const handleConstraintUpload = async (file: File) => {
    setBusy(true);
    try {
      const next = await uploadConstraintDataset(file);
      setWorkspace(next);
      const constraint = next.constraint_dataset;
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: [
            `已加载约束数据 **${file.name}**，识别到 ${constraint.layer_count} 个约束图层。`,
            constraint.layers.length ? `图层：${constraint.layers.join("、")}` : "",
            ...constraint.warnings.map((warning) => `- ${warning}`),
          ].filter(Boolean).join("\n"),
        },
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "未知错误";
      setMessages((current) => [...current, { id: crypto.randomUUID(), role: "assistant", content: `约束数据上传失败：${message}`, error: true }]);
    } finally {
      setBusy(false);
    }
  };

  const handlePolicyReportGenerate = async () => {
    if (busy) return;
    const activeModelId = selectedModelId || workspace?.default_model_id || "local-rules";
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: POLICY_REPORT_PROMPT,
    };
    const assistantId = crypto.randomUUID();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      events: [
        {
          phase: "understand",
          title: "理解报告生成任务",
          detail: "准备基于候选地块分析结果和本地政策参考文档，生成《LLM驱动的城镇开发边界调整政策》PDF。",
          status: "running",
          metadata: {},
        },
      ],
      pending: true,
    };
    setActiveTab("chat");
    setMessages((current) => [...current, userMessage, assistantMessage]);
    setBusy(true);
    try {
      updateAssistant(assistantId, (message) => ({
        ...message,
        events: [
          ...(message.events ?? []).map((event) => ({ ...event, status: "completed" as const })),
          {
            phase: "action",
            title: "调用政策报告生成工具",
            detail: "读取最近一次候选地块分析结果，结合本地政策参考库生成调整说明、政策依据和优化策略文本。",
            status: "running",
            metadata: { model_id: activeModelId },
          },
        ],
      }));
      const artifact = await generatePolicyReport(activeModelId);
      updateAssistant(assistantId, (message) => ({
        ...message,
        events: [
          ...(message.events ?? []).map((event) => ({ ...event, status: "completed" as const })),
          {
            phase: "observe",
            title: "生成PDF成果",
            detail: `已形成政策报告PDF：${artifact.name}，准备进入文档预览页。`,
            status: "completed",
            metadata: { artifact_id: artifact.id },
          },
        ],
      }));
      const next = await refreshWorkspace();
      const freshArtifact = next.artifacts.find((item) => item.id === artifact.id) ?? artifact;
      selectArtifact(freshArtifact);
      setActiveTab("policy");
      updateAssistant(assistantId, (message) => ({
        ...message,
        content: `已生成《LLM驱动的城镇开发边界调整政策》PDF：${artifact.name}。我已切换到“政策文件”页签进行预览，右侧仍可下载该文件。`,
        pending: false,
        events: [
          ...(message.events ?? []).map((event) => ({ ...event, status: "completed" as const })),
          {
            phase: "answer",
            title: "跳转到政策文件页签",
            detail: "生成文档已作为成果文件加载到政策文件预览页。",
            status: "completed",
            metadata: { artifact_id: artifact.id },
          },
        ],
      }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "未知错误";
      updateAssistant(assistantId, (item) => ({
        ...item,
        content: `政策PDF生成失败：${message}`,
        pending: false,
        error: true,
        events: [
          ...(item.events ?? []).map((event) => event.status === "running" ? { ...event, status: "error" as const } : event),
          {
            phase: "error",
            title: "报告生成失败",
            detail: message,
            status: "error",
            metadata: {},
          },
        ],
      }));
    } finally {
      setBusy(false);
    }
  };

  const handleReset = async () => {
    setBusy(true);
    try {
      const next = await resetWorkspace();
      setWorkspace(next);
      setMessages([WELCOME_MESSAGE]);
      setSelectedArtifact(null);
      setArtifactDetail(null);
      setActiveTab("chat");
      setMapData(null);
      setLastDecisions([]);
      setDataVersion((version) => version + 1);
    } finally {
      setBusy(false);
    }
  };

  const shellStyle = {
    "--left-width": `${sidebarCollapsed ? 64 : leftWidth}px`,
    "--right-width": `${previewOpen ? rightWidth : 0}px`,
    "--left-resizer-width": `${sidebarCollapsed ? 0 : 8}px`,
    "--right-resizer-width": `${previewOpen ? 8 : 0}px`,
  } as CSSProperties;

  return (
    <div
      className={`app-shell ${sidebarCollapsed ? "sidebar-is-collapsed" : ""} ${previewOpen ? "preview-is-open" : ""}`}
      style={shellStyle}
    >
      <LeftSidebar
        collapsed={sidebarCollapsed}
        workspace={workspace}
        busy={busy}
        selectedModelId={selectedModelId}
        onToggle={() => setSidebarCollapsed((value) => !value)}
        onUpload={handleUpload}
        onUploadConstraint={handleConstraintUpload}
        onReset={handleReset}
      />
      <div
        className={`panel-resizer left-resizer ${sidebarCollapsed ? "disabled" : ""}`}
        aria-label="调整左侧面板宽度"
        title="拖动调整左侧面板宽度"
        onMouseDown={() => !sidebarCollapsed && setResizing("left")}
      />
      <section className="main-column">
        <MainTabs
          active={activeTab}
          hasSpatial={!!workspace?.has_spatial_data}
          onChange={setActiveTab}
        />
        <div className="main-tab-body">
          <div className="main-tab-panel" hidden={activeTab !== "chat"}>
            <ChatPanel
              messages={messages}
              busy={busy}
              blockCount={workspace?.block_count ?? 0}
              models={workspace?.models ?? []}
              selectedModelId={selectedModelId}
              onModelChange={setSelectedModelId}
              onAddModel={() => setAddModelOpen(true)}
              onSend={handleSend}
              onOpenPreview={() => setPreviewOpen(true)}
            />
          </div>
          <div className="main-tab-panel" hidden={activeTab !== "data"}>
            <DataTableView
              datasetName={workspace?.dataset_name ?? ""}
              blockCount={workspace?.block_count ?? 0}
              decisions={lastDecisions}
              dataVersion={dataVersion}
            />
          </div>
          <div className="main-tab-panel" hidden={activeTab !== "policy"}>
            <PolicyDocumentView report={latestPolicyReport} />
          </div>
          {activeTab === "map" && !!workspace?.has_spatial_data && (
            <div className="main-tab-panel">
              <MapView data={mapData} />
            </div>
          )}
        </div>
      </section>
      <div
        className={`panel-resizer right-resizer ${previewOpen ? "" : "disabled"}`}
        aria-label="调整右侧面板宽度"
        title="拖动调整右侧面板宽度"
        onMouseDown={() => previewOpen && setResizing("right")}
      />
      <AnalysisSidebar
        open={previewOpen}
        workspace={workspace}
        decisions={lastDecisions}
        busy={busy}
        artifacts={workspace?.artifacts ?? []}
        selectedModelId={selectedModelId}
        onRunAnalysis={() => handleSend("哪些街区适合调入开发边界？")}
        onGeneratePolicyReport={handlePolicyReportGenerate}
        onClose={() => setPreviewOpen(false)}
      />
      <AddModelModal
        open={addModelOpen}
        onClose={() => setAddModelOpen(false)}
        onCreated={(result) => {
          setWorkspace(result.workspace);
          setSelectedModelId(result.model.id);
          setAddModelOpen(false);
        }}
      />
    </div>
  );
}
