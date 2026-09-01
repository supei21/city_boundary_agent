import { FormEvent, useEffect, useState } from "react";

import { createUserModel } from "../api";
import type { CreateUserModelResponse } from "../types";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (result: CreateUserModelResponse) => void;
}

export function AddModelModal({ open, onClose, onCreated }: Props) {
  const [label, setLabel] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError("");
    setSubmitting(false);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, submitting, onClose]);

  if (!open) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = {
      label: label.trim(),
      model: model.trim(),
      baseUrl: baseUrl.trim(),
      apiKey: apiKey.trim(),
    };
    if (!trimmed.label || !trimmed.model || !trimmed.apiKey || !trimmed.baseUrl) {
      setError("请填写全部字段");
      return;
    }
    if (!/^https?:\/\//i.test(trimmed.baseUrl)) {
      setError("网址需以 http:// 或 https:// 开头");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      onCreated(await createUserModel({
        label: trimmed.label,
        model: trimmed.model,
        base_url: trimmed.baseUrl,
        api_key: trimmed.apiKey,
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
      setSubmitting(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !submitting) onClose();
      }}
    >
      <form className="add-model-modal" onSubmit={submit} aria-label="添加模型">
        <h2>添加模型</h2>
        <p className="modal-subtitle">可填写 ChatGPT / OpenAI 兼容接口；API Key 仅保存在本机 runtime 目录。</p>
        <div className="modal-field">
          <label htmlFor="um-label">显示名称</label>
          <input
            id="um-label"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="例如：ChatGPT API"
            autoFocus
            maxLength={40}
          />
        </div>
        <div className="modal-field">
          <label htmlFor="um-model">模型名</label>
          <input
            id="um-model"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="例如：gpt-4o-mini 或 gpt-4.1"
            maxLength={80}
          />
        </div>
        <div className="modal-field">
          <label htmlFor="um-url">网址</label>
          <input
            id="um-url"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="例如：https://api.openai.com/v1"
            maxLength={200}
          />
        </div>
        <div className="modal-field">
          <label htmlFor="um-key">API Key</label>
          <input
            id="um-key"
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="sk-…"
            maxLength={300}
          />
        </div>
        {error && <p className="modal-error">{error}</p>}
        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={submitting}>取消</button>
          <button type="submit" className="primary" disabled={submitting}>
            {submitting ? "保存中…" : "保存"}
          </button>
        </div>
      </form>
    </div>
  );
}
