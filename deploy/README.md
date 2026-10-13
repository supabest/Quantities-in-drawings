# 部署指南

## 轻量云服务器部署（推荐：Docker）

### 前置要求
- 服务器已安装 Docker 和 Docker Compose
- 建议配置：1核2GB 或以上

### 部署步骤

1. **上传项目到服务器**
   ```bash
   # 在本地打包
   tar -czf decoration-quant.tar.gz /path/to/decoration-quantity --exclude=__pycache__ --exclude=.git

   # 上传到服务器
   scp decoration-quant.tar.gz user@your-server:~
   ```

2. **服务器上解压并启动**
   ```bash
   ssh user@your-server
   mkdir -p /opt/decoration-quant
   tar -xzf decoration-quant.tar.gz -C /opt/decoration-quant
   cd /opt/decoration-quant

   # 设置管理员密码
   export SECRET_KEY="your-strong-secret-key"
   export ADMIN_PASSWORD="your-admin-password"

   # 启动
   docker-compose up -d
   ```

3. **配置反向代理（推荐 Nginx）**

   安装 Nginx 后，创建 `/etc/nginx/sites-available/decoration-quant`：

   ```nginx
   server {
       listen 80;
       server_name your-domain.com;  # 替换为实际域名

       client_max_body_size 32M;

       location / {
           proxy_pass http://127.0.0.1:5000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }

       location /static/ {
           alias /opt/decoration-quant/app/static/;
           expires 7d;
       }
   }
   ```

   ```bash
   # 启用站点
   ln -s /etc/nginx/sites-available/decoration-quant /etc/nginx/sites-enabled/
   nginx -t && systemctl reload nginx

   # 配置 HTTPS（推荐使用 certbot）
   certbot --nginx -d your-domain.com
   ```

## 直接部署（无 Docker）

### 前置要求
- Python 3.9+
- pip

### 安装步骤

```bash
# 1. 安装系统依赖（OpenCV 需要）
# Ubuntu/Debian:
sudo apt-get update
sudo apt-get install -y python3-pip python3-opencv libgl1-mesa-glx

# 2. 克隆/上传项目
cd decoration-quantity

# 3. 安装 Python 依赖
pip3 install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，修改 SECRET_KEY 和 ADMIN_PASSWORD

# 5. 运行应用
python3 run.py
# 或使用生产模式
gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 run:app
```

## 静态发布（第 1 档，零月费）

把已封账的工程量表发成**只读**静态站，访问者不依赖开发机开机：

```bash
./venv/bin/python scripts/build_decoration_workbook.py   # 重算 xlsx（破账不产出）
./venv/bin/python scripts/build_static_site.py          # 建 site/public/
./deploy/deploy_pages.sh                                 # 上面两步 + 发布 + 线上自检
```

- 线上：`https://decoration-quantity.pages.dev/`（`/boq` `/pending` `/notes` `/download/…xlsx`）。
- 首次建项目（仅一次）：`npx wrangler pages project create decoration-quantity --production-branch main --force`。
- **为何云端不自动构建**：几何链要读 `装修图纸/`（刻意不入库）且 venv 630MB 原生库，Pages 构建环境装不下；
  所以链在本地跑，**产物 `site/public/` 入库**，Pages 只发静态文件（接 Git 集成时 output dir=`site/public`、build command 留空）。
- 能力边界：只能看/筛选/下载；**上传图纸重算、备注回填不在此**（需云端常驻服务）。
- 口径与硬约束见 `docs/QUANTITY_TAKEOFF_MANUAL.md` §7.7；回归：`tests/test_static_site.py`。

## 安全建议
1. **修改默认管理员密码**
2. **设置强 SECRET_KEY**
3. **配置 HTTPS**
4. **定期备份数据库 `app.db`**
5. **配置防火墙，仅开放必要端口（80/443）**
