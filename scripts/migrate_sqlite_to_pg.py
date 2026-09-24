#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5a · SQLite → PostgreSQL 简易数据导入脚本。

用法：
    # 1. 启动目标 PG（docker compose up -d db）
    # 2. 让 Alembic 建表（跳过 seed）
    DATABASE_URL=postgresql://quant:quant_dev_pw@localhost:5432/decoration_quant \
        SKIP_DB_CREATE_ALL=1 FLASK_APP=run.py flask db upgrade
    # 3. 跑本脚本导数据
    DATABASE_URL=postgresql://quant:quant_dev_pw@localhost:5432/decoration_quant \
        python scripts/migrate_sqlite_to_pg.py [--src ./app.db] [--truncate]

设计原则：
    - SQLAlchemy 双方言，不依赖 psql 命令行
    - 按 FK 顺序插入（users → projects → drawings → measurements ...）
    - --truncate 支持重跑；无该参数则要求目标表为空
    - 保留原 PK（SQLite 自增 ID 直连 PG 后需 setval 校准）
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from sqlalchemy import create_engine, text, inspect, Boolean, DateTime
from sqlalchemy.orm import sessionmaker

# 与 app.models 保持一致的导入顺序，避免 SQLAlchemy 元数据缺失
from app import create_app, db  # noqa: E402
from app.models import (  # noqa: E402
    User, Project, Drawing, Measurement, Report,
    HistoricalProject, KnowledgeBaseEntry, SelfCheckItem,
)
from app.orchestrator.task_state import QuantityTask  # noqa: E402


# FK 依赖顺序：先父后子
TABLE_ORDER = [
    ('users', User),
    ('projects', Project),
    ('drawings', Drawing),
    ('measurements', Measurement),
    ('reports', Report),
    ('historical_projects', HistoricalProject),
    ('knowledge_base_entries', KnowledgeBaseEntry),
    ('self_check_items', SelfCheckItem),
    ('quantity_tasks', QuantityTask),
]


def _coerce_row(col_types, row):
    """SQLite → PG 类型强转。

    SQLite 无真正的布尔/日期类型：布尔存为 0/1 整数，日期存为字符串。
    PG 严格类型会拒绝将整数 1 写入 boolean 列（DatatypeMismatch）。
    """
    out = {}
    for k, v in row.items():
        t = col_types.get(k)
        if v is None or t is None:
            out[k] = v
        elif isinstance(t, Boolean):
            # 0/1 整数 或 't'/'f'/'1'/'0' → bool
            out[k] = bool(v) if isinstance(v, int) else \
                (v.lower() in ('t', 'true', '1', 'yes') if isinstance(v, str) else v)
        elif isinstance(t, DateTime) and isinstance(v, str):
            # ISO 字符串 → datetime（psycopg2 对带空格格式也能cast，但显式更稳）
            try:
                from datetime import datetime as _dt
                out[k] = _dt.fromisoformat(v)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out


def _copy_table(src_session, dst_session, table_name, model, truncate=False):
    """从 SQLite 读取指定表的所有行并写入 PG，保留 PK，按目标列类型强转。"""
    src_eng = src_session.bind
    with src_eng.connect() as conn:
        rows = conn.execute(text(f'SELECT * FROM {table_name}')).mappings().all()

    if not rows:
        print(f'  {table_name:28s} 0 行，跳过')
        return 0

    if truncate:
        dst_session.execute(text(f'TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE'))

    existing = dst_session.execute(text(f'SELECT COUNT(*) FROM {table_name}')).scalar()
    if existing > 0:
        print(f'  {table_name:28s} ⚠️ 目标已有 {existing} 行，加 --truncate 重跑')
        return 0

    # 目标列类型（用于 SQLite→PG 强转）
    col_types = {c.name: c.type for c in model.__table__.columns}

    cols = list(rows[0].keys())
    placeholders = ', '.join(f':{c}' for c in cols)
    insert_sql = text(f'INSERT INTO {table_name} ({", ".join(cols)}) VALUES ({placeholders})')
    dst_session.execute(insert_sql, [_coerce_row(col_types, dict(r)) for r in rows])

    # 校准 PG 序列，避免后续 INSERT 撞 PK
    dst_session.execute(text(
        f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {table_name}), 1))"
    ))
    print(f'  {table_name:28s} {len(rows):>4d} 行 ✓')
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', default=os.path.join(BASE, 'app.db'), help='SQLite 源文件（默认 ./app.db）')
    ap.add_argument('--truncate', action='store_true', help='导入前 TRUNCATE 目标表（重跑用）')
    args = ap.parse_args()

    dst_url = os.environ.get('DATABASE_URL')
    if not dst_url or not dst_url.startswith('postgresql'):
        sys.exit('❌ 请先 export DATABASE_URL=postgresql://... 指向目标 PG')

    src_url = f'sqlite:///{os.path.abspath(args.src)}'
    if not os.path.exists(args.src):
        sys.exit(f'❌ SQLite 源文件不存在: {args.src}')

    src_engine = create_engine(src_url)
    dst_engine = create_engine(dst_url)

    # 目标 schema 检查
    insp = inspect(dst_engine)
    missing = [t for t, _ in TABLE_ORDER if t not in insp.get_table_names()]
    if missing:
        sys.exit(f'❌ 目标 PG 缺表：{missing}\n先跑：flask db upgrade')

    src_session = sessionmaker(bind=src_engine)()
    dst_session = sessionmaker(bind=dst_engine)()

    print(f'源  : {src_url}')
    print(f'目标: {dst_url}')
    print(f'模式: {"TRUNCATE + 导入" if args.truncate else "只导入空表"}')
    print('-' * 60)

    total = 0
    for table_name, model in TABLE_ORDER:
        try:
            total += _copy_table(src_session, dst_session, table_name, model, args.truncate)
            dst_session.commit()
        except Exception as exc:
            dst_session.rollback()
            print(f'  {table_name:28s} ✗ {type(exc).__name__}: {exc}')
            raise

    print('-' * 60)
    print(f'✅ 共迁移 {total} 行')


if __name__ == '__main__':
    main()
