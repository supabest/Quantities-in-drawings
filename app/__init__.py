from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录以访问此页面。'
login_manager.login_message_category = 'warning'

# Phase 5a：Alembic 封装，支持 SQLite ↔ PG 双方言
migrate = Migrate()


def _admin_initial_password(app):
    """首次 seed 用管理员密码：优先环境变量；未配置则随机生成一次性密码，
    仅在本启动日志打印一次（不落库明文、不写文件），登录后请立即修改。"""
    pwd = app.config.get('ADMIN_PASSWORD')
    if pwd:
        return pwd
    import secrets
    pwd = secrets.token_urlsafe(12)
    app.logger.warning(
        '未设置 ADMIN_PASSWORD：已为 admin 生成一次性密码：%s '
        '（仅此一次显示，请立即登录并修改）', pwd)
    return pwd


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Ensure upload and reports directories exist
    import os
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['REPORTS_FOLDER'], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    from app.auth import auth_bp
    from app.routes import main_bp
    from app.api import api_bp
    from app.orchestrator.api import orchestrator_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(orchestrator_bp, url_prefix='/api/orchestrator')

    with app.app_context():
        from app import models
        # Phase 5a：`flask db migrate/upgrade` 时设 SKIP_DB_CREATE_ALL=1，
        # 避免与 Alembic 自治生成产生“无 diff”的无头迁移（同时跳过 seed）。
        if os.environ.get('SKIP_DB_CREATE_ALL'):
            return app
        db.create_all()
        # Create default admin if not exists
        from app.models import User, KnowledgeBaseEntry
        admin = User.query.filter_by(is_admin=True).first()
        if not admin:
            admin = User(
                email=app.config['ADMIN_EMAIL'],
                username='admin',
                is_admin=True,
                is_approved=True
            )
            admin.set_password(_admin_initial_password(app))
            db.session.add(admin)
            db.session.commit()

        # Seed knowledge base entries — idempotent per title, safe on existing DBs.
        # 来源文档：
        #   - AI工程量计算注意事项-知识库.docx（旧版，已被读图算量知识库 v1.0 取代）
        #   - AI工程量计算读图算量注意事项.docx（v1.0，2026-09 吸收）
        KB_SEEDS = [
            # ── 旧版知识库种子（规则已并入规范，留档）──
            dict(
                title='固定柜台/服务台计量单位区分',
                category='rule',
                scenario='所有含固定柜台的装修项目',
                description='有大样做法的柜子按以下规则计量，三类不可混用：\n'
                            '• 衣柜/储物柜 → 正立面投影面积(m²)\n'
                            '• 厨柜/台盆柜 → 延展长度(m)\n'
                            '• 服务台 → 个（注明L×W×H）\n\n'
                            '输出清单前须核对"项目名称"与"单位"列是否匹配，'
                            '避免厨柜误套面积单价。',
                source='AI工程量计算注意事项-知识库.docx',
                status='archived',
                merged_to='v3 §3.1第6分部 / §5.2'
            ),
            dict(
                title='地面多材质HATCH分区规则',
                category='rule',
                scenario='同房间不同材质地面的图纸识别',
                description='HATCH填充仅用于识别材质分界线位置，不作为工程量数值来源：\n'
                            '• 用HATCH识别各材质分区边界\n'
                            '• 再用平面图净尺寸标注重新计算各分区面积\n'
                            '• 禁止直接读取HATCH填充面积作为工程量\n\n'
                            '计算结果须与标注尺寸交叉核对，偏差>5%以标注为准。',
                source='AI工程量计算注意事项-知识库.docx',
                status='archived',
                merged_to='v3 §六(对数原则) / 自检清单'
            ),
            # ── 读图算量知识库 v1.0 吸收（新增规则，待并入引擎）──
            dict(
                title='无立面部位四级判断优先级',
                category='rule',
                scenario='墙面算量中，墙段在图纸中没有对应立面图时',
                description='墙段无对应立面图时禁止直接跳过，按优先级逐步判断：\n'
                            '① 平面图有材质标注 → 按标注材质计量（平面尺寸×层高），🟡中\n'
                            '② 相邻同类房间立面有相同部位 → 参照做法，备注【参照XX立面图】，🟡中\n'
                            '③ 设计说明/材料表有通用做法 → 按通用标准执行，'
                            '备注【按设计说明统一做法】，🟡中\n'
                            '④ 无任何参考 → 按乳胶漆计量（最保守通用做法），🔴低，列入待确认\n\n'
                            '输出清单时须单独附【无立面部位汇总表】（部位+处理依据+置信度）。',
                source='AI工程量计算读图算量注意事项.docx v1.0 §3',
                status='active'
            ),
            dict(
                title='易漏算部位核查清单（多无独立立面图）',
                category='pitfall',
                scenario='平立面交叉核实、清单输出前的遗漏检查',
                description='以下部位往往没有独立立面图，但面积不可忽略，逐项核查：\n'
                            '• 入户短墙\n'
                            '• 转角三角区\n'
                            '• 卫生间隔墙侧面\n'
                            '• 柱体侧面：展开四面，正面通常含于室内立面，其余三面单独补项\n'
                            '• 管井/烟道侧面：按乳胶漆或瓷砖（视房间类型），🟡中\n'
                            '• 楼梯间侧墙：斜面按投影面积，备注须乘坡度系数\n'
                            '• 卫生间隔断上方高窗区（<300mm）：随墙面做法补入\n'
                            '• 门后背墙：属正常墙面，照算不得遗漏',
                source='AI工程量计算读图算量注意事项.docx v1.0 §2.1/§3.2',
                status='active'
            ),
            dict(
                title='柜体背后墙面扣减规则',
                category='rule',
                scenario='固定柜体紧贴墙面安装时的墙面面积计算',
                description='柜体紧贴墙面安装时，该段墙面面积须按"柜体宽×高"从墙面清单中'
                            '扣减，不得与柜体正立面投影面积重复计算。\n\n'
                            '柜体底部地面：施工需铺设的照常计算；厂家负责底部处理的，备注注明。\n'
                            '甲供柜体：备注注明"甲供"，材料费归零仅计安装。',
                source='AI工程量计算读图算量注意事项.docx v1.0 §4.2',
                status='active'
            ),
            dict(
                title='门窗计量默认规则与玻璃隔断拆分',
                category='rule',
                scenario='门窗工程（第4分部）计量方式选择',
                description='• 门窗面积按合同约定（洞口面积 or 框外围面积）；'
                            '无约定时默认按洞口面积，备注注明\n'
                            '• 同一图纸多种门型时逐一核对门窗表，禁止目测尺寸代替\n'
                            '• 玻璃隔断含开启扇时：门扇单独列项，固定玻璃部分列入玻璃隔断',
                source='AI工程量计算读图算量注意事项.docx v1.0 §5.2',
                status='active'
            ),
            dict(
                title='踢脚线多材质分区与高度核对',
                category='rule',
                scenario='同一房间多种踢脚线材质、踢脚线高度取值',
                description='• 不同材质踢脚线（如木踢脚+瓷砖踢脚同房间）分别列项\n'
                            '• 长度公式不变：净周长−门洞净宽合计−固定柜体占用段\n'
                            '• 高度须与立面图核对（常见60/80/100/150mm），'
                            '不得默认统一高度，🟡中',
                source='AI工程量计算读图算量注意事项.docx v1.0 §5.3',
                status='active'
            ),
            dict(
                title='灯槽/窗帘盒计量须先与用户确认',
                category='edge_case',
                scenario='收到天花图，图中存在灯槽或窗帘盒时',
                description='【交互规则】输出清单前须主动询问用户：\n'
                            '本项目灯槽/窗帘盒是单独按米计量，还是综合在天花面积内列项？\n'
                            '用户确认前不得出清单，不得自行假设。\n\n'
                            '• 方式A（单独计量）：灯槽按延米列项，注明截面做法（宽×深）'
                            '及大样图编号；窗帘盒同理\n'
                            '• 方式B（综合计入天花）：灯槽内侧展开面积折算并入天花面积，'
                            '备注【含灯槽展开面积×.×m²】\n'
                            '• 无大样图时：截面尺寸均为估算值，🟡中，'
                            '备注【灯槽截面尺寸待大样图确认】',
                source='AI工程量计算读图算量注意事项.docx v1.0 §5.1(四)',
                status='active'
            ),
            dict(
                title='多格式图纸权威域分工与融合去重',
                category='rule',
                scenario='同一图纸存在 DWG/DXF/PDF/图片多种格式时的解析与交叉验证',
                description='多格式并行解析时按权威域分工，避免重复计算与语义冲突：\n'
                            '• DXF/DWG = 几何与图层语义权威（边界坐标、面积、HATCH图案名、'
                            '图层名、块名）\n'
                            '• 矢量PDF = 文字语义权威（房间名、材料编号、尺寸标注、材料表）；'
                            '须为CAD导出的矢量PDF，扫描版等同图片\n'
                            '• 光栅图片 = 兜底与标注渲染，不作为尺寸权威源\n\n'
                            '融合去重：同一实体多来源时按身份键归并（CAD handle / bbox重叠度），'
                            '多来源记录在案，只出一条清单项。\n'
                            '冲突仲裁：几何定形、文字定义；几何与标注尺寸矛盾时以标注为准，'
                            '偏差>5%输出审计提示。\n\n'
                            '注意：PDF导出会丢失图层信息（影响天花线/墙线判别），'
                            '图层语义以DXF为准。详见 docs/CAD_PARSING.md §九。',
                source='架构设计讨论 + AI工程量计算读图算量注意事项.docx v1.0 §5.1(三)',
                status='active'
            ),
        ]
        existing_titles = {e.title for e in KnowledgeBaseEntry.query.all()}
        for seed in KB_SEEDS:
            if seed['title'] not in existing_titles:
                db.session.add(KnowledgeBaseEntry(**seed))
        db.session.commit()

    return app
