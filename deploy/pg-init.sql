-- Phase 5a · PostgreSQL 初始化脚本
-- docker-entrypoint-initdb.d 首次启动时自动执行
-- 追加一个独立的测试库，避免测试污染开发数据

CREATE DATABASE decoration_quant_test;
GRANT ALL PRIVILEGES ON DATABASE decoration_quant_test TO quant;
