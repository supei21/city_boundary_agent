import { Bot, Trash2 } from "lucide-react";

import type { ModelOption } from "../types";

const ADD_MODEL_OPTION = "__add__";

interface Props {
  models: ModelOption[];
  value: string;
  disabled: boolean;
  onChange: (modelId: string) => void;
  onAddModel?: () => void;
  onDeleteModel?: (modelId: string) => void;
}

export function ModelSelector({ models, value, disabled, onChange, onAddModel, onDeleteModel }: Props) {
  const selected = models.find((model) => model.id === value);
  const canDelete = value.startsWith("custom-");

  const remove = () => {
    if (!canDelete || !onDeleteModel) return;
    if (window.confirm(`确定删除模型“${selected?.label ?? value}”吗？`)) {
      onDeleteModel(value);
    }
  };

  return (
    <div className="model-selector-wrap">
      <label className="model-selector" title={selected?.description ?? "选择回答模型"}>
        <Bot size={14} aria-hidden="true" />
        <select
          value={value}
          aria-label="选择回答模型"
          disabled={disabled || !models.length}
          onChange={(event) => {
            const next = event.target.value;
            if (next === ADD_MODEL_OPTION) {
              event.currentTarget.value = value;
              onAddModel?.();
              return;
            }
            onChange(next);
          }}
        >
          {models.map((model) => (
            <option key={model.id} value={model.id} disabled={!model.ready}>
              {model.label}{model.ready ? "" : "（未配置）"}
            </option>
          ))}
          <option value={ADD_MODEL_OPTION} disabled={disabled}>＋ 添加模型…</option>
        </select>
      </label>
      {canDelete && (
        <button
          type="button"
          className="delete-model-button"
          onClick={remove}
          disabled={disabled}
          title="删除当前自定义模型"
          aria-label="删除当前自定义模型"
        >
          <Trash2 size={14} />
        </button>
      )}
    </div>
  );
}
