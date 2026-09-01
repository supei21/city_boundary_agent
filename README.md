# 城镇开发边界智能体 Demo

这是一个用于阶段演示的城镇开发边界分析智能体。项目采用 React + TypeScript 三栏工作台、FastAPI 流式接口，以及轻量 ReAct 智能体流程。

大模型负责理解意图、选择工具和组织回答；训练完成的 XGBoost 模型负责给候选地块输出开发变化概率；数据校验、适宜性评分、动态适配指数、约束判断与调入调出结论由可测试的确定性代码完成。界面展示可公开的执行步骤和工具结果，不展示模型隐藏思维链。

## 界面与能力

- 左侧：数据上传、分析功能与成果文件入口。
- 中间：对话、流式执行过程和最终回答。
- 右侧：独立文件预览区，可切换、滚动和下载。
- 支持 JSON、CSV、SHP ZIP 和内置示例数据。
- SHP 上传后可在右侧地图中查看地块位置、属性和分析结果。
- 候选调整地块可导出为带分析字段的 SHP ZIP。
- 将外部训练工程 `E:\VscodeMyPrograma\boundary_agent_demo\models` 中的 XGBoost 模型封装为智能体工具。
- 计算模型开发概率、空间适宜性、规划匹配度、约束成本和动态适配指数，并提供全市与区内排名。
- 地图支持按一级调入、二级调入、调出、内部更新和人工复核筛选。
- 支持查看、分析、比较和筛选候选街区。
- 支持在对话输入框中切换已配置的大模型或本地规则模式。
- 预留“生成调入调出图”和“生成分析报告”入口。
- 模型不可用时自动使用本地规则流程，确保 Demo 可演示。

## 项目结构

```text
boundary_agent_demo/
├─ backend/                 # FastAPI 接口与流式响应
├─ boundary_agent/          # 智能体、工具、规则和领域模型
├─ frontend/                # React + TypeScript 用户界面
├─ config/                  # 分析规则与模型配置
├─ data/
│  ├─ sample/               # 可提交的演示数据
│  └─ input/                # 本地业务输入数据
├─ prompts/                 # 提示词模板
├─ scripts/                 # 统一启动入口
├─ tests/                   # 自动化测试
├─ runtime/                 # 日志、结果和导出文件，不放源码
├─ docs/                    # 项目说明与汇报材料
└─ legacy/                  # 旧版 Streamlit 界面，仅供对照
```

各目录的文件归属规则见 [docs/项目目录说明.md](docs/项目目录说明.md)。

## 安装

```powershell
cd "E:\\VscodeMyPrograma\\城镇开发边界\\boundary_agent_demo"
E:\\miniconde\\envs\\langchain_env\\python.exe -m pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
```

## 配置

- `config/analysis.yaml`：评分权重、阈值和约束规则。
- `config/analysis.yaml` 中的 `trained_model.model_dir`：训练模型目录，默认读取 `xgb_boundary.json`、`feature_names.json` 和 `metrics.json`。
- `config/model_example.py`：可提交到版本库的模型配置示例。
- `config/model_local.py`：本机模型密钥与地址，已由 `.gitignore` 排除。

首次配置模型时，可参考 `config/model_example.py` 创建 `config/model_local.py`。不要把真实 API Key 写入 README、示例文件或版本库。

训练模型工具默认启用，不依赖 `xgboost` 或 `joblib` 运行时包，而是直接读取训练工程保存的 JSON 模型：

```yaml
trained_model:
  enabled: true
  model_dir: "E:\\VscodeMyPrograma\\boundary_agent_demo\\models"
```

在对话中询问“哪些地块更可能调入”“用模型给候选地块排序”时，智能体会先调用 `score_blocks_with_trained_model` 或在 `analyze_blocks`、`identify_candidate_blocks` 中自动补充 `development_probability`，再进入动态适配综合判断。

需要在界面中增加多个模型时，在 `config/model_local.py` 中配置 `MODEL_PROFILES`。每个档案会继承 `MODEL_CONFIG` 中未覆盖的连接参数：

```python
MODEL_PROFILES = {
    "reasoning": {
        "label": "推理模型",
        "model": "your-reasoning-model",
    },
    "fast": {
        "label": "快速模型",
        "model": "your-fast-model",
    },
}
```

## 运行

先构建前端，再启动统一服务：

```powershell
E:\\miniconde\\envs\\langchain_env\\python.exe scripts\\run_demo.py
```

浏览器打开 `http://127.0.0.1:8000`。

前端开发模式：

```powershell
# 终端 1：API
E:\\miniconde\\envs\\langchain_env\\python.exe -m uvicorn backend.main:app --reload --port 8000

# 终端 2：前端
cd frontend
npm run dev
```

前端开发地址为 `http://127.0.0.1:5173`。

命令行演示：

```powershell
E:\\miniconde\\envs\\langchain_env\\python.exe scripts\\run_cli.py
```

旧版 Streamlit 界面仅用于迁移对照：

```powershell
E:\\miniconde\\envs\\langchain_env\\python.exe -m streamlit run legacy\\streamlit_app.py
```

## 测试

```powershell
E:\\miniconde\\envs\\langchain_env\\python.exe -m unittest discover -s tests -v
cd frontend
npm run build
```

## 数据要求

- JSON 可以是街区数组，也可以是包含 `blocks` 数组的对象。
- CSV 列名应与 `boundary_agent/models.py` 中的 `BlockInput` 对应。
- Shapefile 应将同名的 `.shp`、`.shx`、`.dbf`、`.prj` 文件放入一个 ZIP。
- 当前空间 Demo 接受 Polygon 或 MultiPolygon 地块，并统一转换为 EPSG:4326 用于网页预览。
- SHP ZIP 使用分块写盘上传，默认上限为 1024MB；地图默认显示候选优先的 8000 个地块，并仅对网页预览采用约2米的拓扑保持简化，完整原始数据仍参与分析和导出。
- JSON/CSV 中 `block_id` 和 `inside_boundary` 为必填字段；SHP缺少 `block_id` 时会依据几何生成稳定编号，缺少边界标识时暂按边界外处理并给出警告。
- 缺失指标不会自动填 0；关键约束缺失时会标记人工复核。

可通过环境变量调整大文件参数：

```powershell
$env:BOUNDARY_MAX_UPLOAD_MB = "2048"
$env:BOUNDARY_MAX_EXTRACTED_MB = "8192"
$env:BOUNDARY_MAP_MAX_FEATURES = "8000"
$env:BOUNDARY_MAP_SIMPLIFY_METERS = "2"
E:\miniconde\envs\langchain_env\python.exe scripts\run_demo.py
```

## 当前模型接入效果

使用 `D:\downloadzhuomian\城镇开发边界\初步筛选调整地块\初步筛选调整地块.zip` 端到端验证时，系统读取 2364 个候选地块、50 个模型特征，并完成模型概率与动态适配排序。当前示例结果为：一级优先调入 159 个，二级优先调入 694 个，保持现状 1511 个。该结果属于模型与规则 Demo 辅助判断，不是正式规划结论。

## 下一阶段

- 将训练工程的 SHAP 解释按地块封装为第二个工具，输出每个候选地块的主要正向/负向驱动因素。
- 接入规划文本知识库与RAG，将默认规划匹配度替换为可追溯的政策需求匹配结果。
- 叠加生态红线、永久基本农田和灾害风险等空间约束。
- 生成调入调出 GeoJSON、GeoPackage 或 SHP ZIP。
- 按多步骤任务流程生成地图和 PDF 分析报告。
