"""项目路径的唯一来源，避免业务模块自行拼接目录。"""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
CONFIG_FILE = PROJECT_ROOT / "config" / "analysis.yaml"
SAMPLE_DATA_FILE = PROJECT_ROOT / "data" / "sample" / "blocks.json"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOGS_DIR = RUNTIME_DIR / "logs"
RESULTS_DIR = RUNTIME_DIR / "results"
EXPORTS_DIR = RUNTIME_DIR / "exports"
UPLOADS_DIR = RUNTIME_DIR / "uploads"
USER_MODELS_FILE = RUNTIME_DIR / "user_models.json"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
DOCS_DIR = PROJECT_ROOT / "docs"
REPOSITORY_ROOT = PROJECT_ROOT.parent.parent
RAG_PROJECT_DIR = REPOSITORY_ROOT / "rag1.0" / "rag1.0"
