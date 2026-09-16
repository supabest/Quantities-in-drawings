import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # SQLite busy 等待 30s：瞬时写锁竞争排队而非报错（生产切 PostgreSQL 后可移除）
    SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 30}}
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
