import { Bot } from "lucide-react";

import type { ModelOption } from "../types";

const ADD_MODEL_OPTION = "__add__";

interface Props {
  models: ModelOption[];
  value: string;
  disabled: boolean;
  onChange: (modelId: string) => void;
  onAddModel?: () => void;
}

export function ModelSelector({ models, value, disabled, onChange, onAddModel }: Props) {
  const selected = models.find((model) => model.id === value);

  return (
    <label className="model-selector" title={selected?.description ?? "选择分析模型"}>
      <Bot size={14} aria-hidden="true" />
      <select
        aria-label="选择分析模型"
        value={value}
        onChange={(event) => {
          const next = event.target.value;
          if (next === ADD_MODEL_OPTION) {
            // 从未把 __add__ 写入受控 value，React 不会重渲染 select，
            // 需要手动把 DOM 选中项恢复到之前的模型。
            event.currentTarget.value = value;
            onAddModel?.();
            return;
          }
          onChange(next);
        }}
        disabled={disabled || !models.length}
      >
        {models.map((model) => (
          <option key={model.id} value={model.id} disabled={!model.ready}>
            {model.label}{model.ready ? "" : "（未配置）"}
          </option>
        ))}
        <option value={ADD_MODEL_OPTION} disabled={disabled}>＋ 添加模型…</option>
      </select>
    </label>
  );
}
