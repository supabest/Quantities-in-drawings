import os
from dotenv import load_dotenv

load_dotenv()


def _default_engine_options(uri: str) -> dict:
    """根据 DB scheme 返 SQLAlchemy engine_options。

    SQLite 保持 busy timeout=30s（开发无依赖时仍能处理瞬时写锁）；
    PostgreSQL 不传 connect_args.timeout（会报错），交给 PG 自己处理并发。
    """
    if uri.startswith('sqlite'):
        return {'connect_args': {'timeout': 30}}
    # postgresql:// / postgresql+psycopg2:// 等
    return {'pool_pre_ping': True, 'pool_size': 10, 'max_overflow': 20}


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = _default_engine_options(SQLALCHEMY_DATABASE_URI)
    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'app', 'static', 'uploads')
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024  # 32 MB max upload
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp', 'tiff', 'dxf'}
    REPORTS_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'app', 'static', 'reports')

    # Pagination
    ITEMS_PER_PAGE = 12

    # Default admin credentials (first run)
    ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL') or 'admin@decoration-quant.com'
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or 'admin123456'

    # 套价系统联动 (decoration-pricing-system)
    PRICING_API_BASE_URL = os.environ.get('PRICING_API_BASE_URL') or 'http://localhost:8000'
    PRICING_API_KEY = os.environ.get('PRICING_API_KEY') or ''

    # ── LLM 审计（Phase 4）—— 复用套价系统的 GLM 调用约定 ──
    # OpenAI 兼容协议；model 以 glm 开头自动路由智谱双端点（按量主端点 + Coding Plan 备端点）
    LLM_API_KEY = os.environ.get('LLM_API_KEY') or ''
    LLM_MODEL = os.environ.get('LLM_MODEL') or 'glm-4.6'
    LLM_MAX_TOKENS = int(os.environ.get('LLM_MAX_TOKENS') or 8000)
    LLM_TIMEOUT_SECONDS = int(os.environ.get('LLM_TIMEOUT_SECONDS') or 180)
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL') or 'https://api.deepseek.com/v1'
    LLM_BASE_URL_ZHIPU = os.environ.get('LLM_BASE_URL_ZHIPU') or 'https://open.bigmodel.cn/api/paas/v4'
    LLM_BASE_URL_ZHIPU_FALLBACK = os.environ.get('LLM_BASE_URL_ZHIPU_FALLBACK') or 'https://open.bigmodel.cn/api/coding/paas/v4'
    LLM_AUDIT_MAX_CALLS = int(os.environ.get('LLM_AUDIT_MAX_CALLS') or 5)
