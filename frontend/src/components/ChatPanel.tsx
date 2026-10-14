import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { PanelRightOpen, Send } from "lucide-react";
import ReactMarkdown from "react-markdown";

import type { ChatMessage, ModelOption } from "../types";
import { DecisionTable } from "./DecisionTable";
import { ModelSelector } from "./ModelSelector";
import { ProcessTrace } from "./ProcessTrace";

const QUICK_PROMPTS = [
  "查看全部街区数据",
  "哪些街区适合调入开发边界？",
  "找出建议调出的边界内街区",
];

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  blockCount: number;
  models: ModelOption[];
  selectedModelId: string;
  onModelChange: (modelId: string) => void;
  onAddModel: () => void;
  onDeleteModel: (modelId: string) => void;
  onSend: (prompt: string, modelId: string) => void;
  onOpenPreview: () => void;
}

export function ChatPanel({
  messages,
  busy,
  blockCount,
  models,
  selectedModelId,
  onModelChange,
  onAddModel,
  onDeleteModel,
  onSend,
  onOpenPreview,
}: Props) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const selectedModel = models.find((model) => model.id === selectedModelId);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const prompt = draft.trim();
    if (!prompt || busy) return;
    setDraft("");
    onSend(prompt, selectedModelId);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <main className="chat-panel">
      <header className="chat-header">
        <div>
          <h1>城镇开发边界智能体</h1>
          <p>{blockCount} 个街区已加载 · {selectedModel?.label ?? "正在读取模型"}</p>
        </div>
        <button className="icon-button preview-open" onClick={onOpenPreview} title="打开分析功能区">
          <PanelRightOpen size={18} />
        </button>
      </header>

      <div className="chat-scroll">
        <div className="conversation">
          {messages.map((message) => (
            <article className={`message ${message.role} ${message.error ? "message-error" : ""}`} key={message.id}>
              <div className="message-label">{message.role === "user" ? "你" : "规划智能体"}</div>
              {message.events && <ProcessTrace events={message.events} />}
              {message.content && (
                <div className="message-content">
                  <ReactMarkdown>{message.content}</ReactMarkdown>
                </div>
              )}
              {message.result?.decisions && <DecisionTable decisions={message.result.decisions} />}
              {message.pending && !message.events?.length && (
                <div className="pending-line"><span /><span /><span />正在连接分析流程</div>
              )}
            </article>
          ))}
          <div ref={endRef} />
        </div>
      </div>

      <div className="composer-area">
        <div className="quick-prompts">
          {QUICK_PROMPTS.map((prompt) => (
            <button key={prompt} onClick={() => onSend(prompt, selectedModelId)} disabled={busy || !blockCount}>{prompt}</button>
          ))}
        </div>
        <form className="composer" onSubmit={submit}>
          <textarea
            aria-label="输入分析问题"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={blockCount ? "询问街区属性、比较结果或候选地块…" : "请先上传街区数据"}
            disabled={!blockCount || busy}
            rows={1}
          />
          <div className="composer-toolbar">
            <ModelSelector
              models={models}
              value={selectedModelId}
              disabled={busy}
              onChange={onModelChange}
              onAddModel={onAddModel}
              onDeleteModel={onDeleteModel}
            />
            <button type="submit" className="send-button" disabled={!draft.trim() || busy || !selectedModelId} title="发送">
              <Send size={17} />
            </button>
          </div>
        </form>
        <p className="demo-note">Demo结果用于技术验证，不能替代法定规划审查。</p>
      </div>
    </main>
  );
}
