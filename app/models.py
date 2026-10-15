from datetime import datetime
import json
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app import db, login_manager


# ── Enumerations ──

DIVISIONS = [
    (1, '楼地面工程', 'm²', ['瓷砖', '石材', '木地板', '地毯', '自流平']),
    (2, '墙柱面工程', 'm²', ['乳胶漆', '瓷砖', '木饰面', '玻璃', '不锈钢', '铝板', '软包', '墙纸']),
    (3, '天棚工程', 'm²', ['平顶乳胶漆', '轻钢龙骨石膏板', '铝扣板', '格栅', '软膜天花']),
    (4, '门窗工程', '樘/m²', ['木门', '玻璃门', '铝合金窗', '玻璃隔断']),
    (5, '踢脚线工程', 'm', ['木踢脚', '瓷砖踢脚', '不锈钢踢脚']),
    (6, '固定柜台工程', 'm²/m/个', ['衣柜', '储物柜', '厨柜', '台盆柜', '服务台']),
    (7, '给排水工程', '套/个', ['面盆龙头', '淋浴龙头', '马桶', '地漏', '洗衣机龙头']),
    (8, '电气工程', '台/个', ['配电箱', '开关', '插座', '灯具', '弱电点位']),
]

DRAWING_TYPES_ORDERED = ['floor_plan', 'elevation', 'ceiling', 'detail']

SOURCE_TAGS = [
    ('图纸实测', '📐', '直接从图纸尺寸标注读取，未经推算', 'high'),
    ('PDF语义提取', '📄', '从PDF矢量文本/几何自动提取（Phase 5c）', 'medium'),
    ('几何推算', '🔢', '由平面图轮廓+层高（或已知尺寸）计算得出', 'medium'),
    ('历史参照', '📋', '参照同类项目历史清单补入，本次图纸未显示', 'medium'),
    ('经验补全', '🧠', '基于项目类型和工程经验判断，图纸未画出', 'medium'),
    ('逻辑推理', '📖', '由其他已知量或设计逻辑推导', 'medium'),
    ('待确认', '❓', '信息不足，须现场或设计师确认方可使用', 'low'),
]

CONFIDENCE_LEVELS = {
    'high': {'label': '高', 'icon': '🟢', 'color': '#52c41a', 'usage': '可直接用于内部组价和对外报价'},
    'medium': {'label': '中', 'icon': '🟡', 'color': '#faad14', 'usage': '建议复核后再用于对外报价'},
    'low': {'label': '低', 'icon': '🔴', 'color': '#ff4d4f', 'usage': '禁止直接用于对外报价，须现场复量或设计师确认'},
}

PROJECT_TYPES = [
    ('residential', '住宅/样板房', 2.80),
    ('commercial', '商业/办公', 3.00),
    ('lobby', '大堂/门厅', 4.50),
    ('showroom', '售楼部/展厅', 4.00),
]

# Base layer requirements per surface material
BASE_LAYER_RULES = {
    '木饰面': '轻钢龙骨（注明间距）+ 多层夹板（注明厚度）',
    '软包': '轻钢龙骨（注明间距）+ 多层夹板（注明厚度）',
    '不锈钢': '型钢架（规格按大样图）+ 夹板基层（注明厚度）',
    '铝板': '型钢架（规格按大样图）+ 夹板基层（注明厚度）',
    '石材地面': '找平层：1:3干硬性水泥砂浆（注明mm厚）；结合层：1:2.5水泥砂浆；背面刷背胶；面层刷防污剂',
    '瓷砖地面': '找平层材料+厚度；瓷砖胶或专用岩板粘结剂',
    '岩板地面': '找平层材料+厚度；瓷砖胶或专用岩板粘结剂',
    '石材干挂': '注明干挂体系：镀锌钢架+不锈钢挂件 或 铝合金龙骨',
    '乳胶漆': '基层处理：满批腻子×遍；底漆1遍；面漆×遍',
    '卫生间瓷砖': '防水层材料+厚度；地面注明防水上翻高度',
}

# Hidden items that must be completed by experience
HIDDEN_ITEMS = [
    {'name': '面盆龙头', 'rooms': ['卫生间', '洗手间', '茶水间'], 'unit': '套', 'source': '经验补全', 'confidence': 'medium'},
    {'name': '淋浴龙头/花洒', 'rooms': ['卫生间'], 'unit': '套', 'source': '经验补全', 'confidence': 'medium'},
    {'name': '马桶（坐便器）', 'rooms': ['卫生间'], 'unit': '个', 'source': '经验补全', 'confidence': 'medium'},
    {'name': '地漏', 'rooms': ['卫生间', '淋浴区', '洗衣房'], 'unit': '个', 'source': '经验补全', 'confidence': 'medium'},
    {'name': '洗衣机龙头', 'rooms': ['洗衣房', '工作阳台'], 'unit': '个', 'source': '经验补全', 'confidence': 'medium'},
    {'name': '配电箱', 'rooms': ['每户'], 'unit': '台', 'source': '经验补全', 'confidence': 'medium'},
    {'name': '开关插座', 'rooms': ['通用'], 'unit': '个', 'source': '历史参照', 'confidence': 'medium'},
    {'name': '弱电点位', 'rooms': ['客厅', '卧室', '前台', '接待区'], 'unit': '个', 'source': '历史参照', 'confidence': 'medium'},
    {'name': '卫生间防水层', 'rooms': ['卫生间'], 'unit': 'm²', 'source': '逻辑推理', 'confidence': 'medium'},
    {'name': '地面水泥砂浆找平层', 'rooms': ['通用'], 'unit': 'm²', 'source': '逻辑推理', 'confidence': 'medium'},
    {'name': '墙面批腻子/底漆', 'rooms': ['通用'], 'unit': 'm²', 'source': '逻辑推理', 'confidence': 'medium'},
]


# ── User Authentication ──

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    company = db.Column(db.String(128))
    phone = db.Column(db.String(20))
    is_admin = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    projects = db.relationship('Project', backref='owner', lazy='dynamic',
                               cascade='all, delete-orphan')
    historical_projects = db.relationship('HistoricalProject', backref='owner', lazy='dynamic',
                                          cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


# ── Project ──

class Project(db.Model):
    __tablename__ = 'projects'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text)
    address = db.Column(db.String(256))
    project_type = db.Column(db.String(32), default='residential')  # residential/commercial/lobby/showroom
    default_floor_height = db.Column(db.Float, default=2.80)
    drawing_type = db.Column(db.String(32), default='floor_plan')
    scale = db.Column(db.Float, default=1.0)
    scale_reference = db.Column(db.String(64))
    status = db.Column(db.String(20), default='pending')
    historical_ref_prompted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    drawings = db.relationship('Drawing', backref='project', lazy='dynamic',
                               cascade='all, delete-orphan')
    measurements = db.relationship('Measurement', backref='project', lazy='dynamic',
                                   cascade='all, delete-orphan')
    report = db.relationship('Report', backref='project', uselist=False,
                             cascade='all, delete-orphan')


# ── Drawing ──

class Drawing(db.Model):
    __tablename__ = 'drawings'

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(256), nullable=False)
    original_name = db.Column(db.String(256), nullable=False)
    file_type = db.Column(db.String(10), nullable=False)
    file_size = db.Column(db.Integer)
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    drawing_type = db.Column(db.String(20), default='floor_plan')
    annotated_path = db.Column(db.String(256))
    process_order = db.Column(db.Integer, default=0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)

    measurements = db.relationship('Measurement', backref='drawing', lazy='dynamic')


# ── Measurement (算量清单项) ──

class Measurement(db.Model):
    """
    工程量清单项 — 对应规范中的清单条目。

    关键字段映射到规范要求：
    - label → 项目名称（如【客厅石材地面】）
    - division_id → 所属分部（1-8）
    - category → 辅助分类（room/wall/door/window/fixture）
    - source_tag → 来源标签（图纸实测/几何推算/历史参照/经验补全/逻辑推理/待确认）
    - confidence_level → 置信度等级（high/medium/low）
    - position_desc → 部位（房间名称+方位+图纸编号）
    - formula_text → 计算式（完整算式+说明）
    - project_features → 项目特征（面层材质+基层做法+施工工艺）
    - material_code → 材料编号（如 CT02）
    - base_layer → 基层做法描述
    - remarks → 备注
    """
    __tablename__ = 'measurements'

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(128))
    division_id = db.Column(db.Integer, default=1)
    category = db.Column(db.String(32))
    area = db.Column(db.Float)
    perimeter = db.Column(db.Float)
    length = db.Column(db.Float)
    width = db.Column(db.Float)
    height = db.Column(db.Float)
    count = db.Column(db.Integer)
    unit = db.Column(db.String(16), default='m²')

    # 规范扩展字段
    source_tag = db.Column(db.String(16), default='图纸实测')
    source_tag_icon = db.Column(db.String(4), default='📐')
    confidence_level = db.Column(db.String(8), default='high')
    confidence_icon = db.Column(db.String(2), default='🟢')
    position_desc = db.Column(db.Text)            # 部位：房间+方位+图纸号
    formula_text = db.Column(db.Text)             # 计算式：完整算式
    project_features = db.Column(db.Text)         # 项目特征
    material_code = db.Column(db.String(32))      # 材料编号
    base_layer = db.Column(db.Text)              # 基层做法
    remarks = db.Column(db.Text)                 # 备注
    data_json = db.Column(db.Text)
    confidence = db.Column(db.Float)

    # Phase 2 区域级并行：区域归属与边界标记
    zone_id = db.Column(db.String(32), index=True)      # 所属区域标识（如 zone_public）
    boundary_type = db.Column(db.String(16))            # zone=区内 / boundary=区界共享

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    drawing_id = db.Column(db.Integer, db.ForeignKey('drawings.id'), nullable=True)

    @property
    def confidence_display(self):
        info = CONFIDENCE_LEVELS.get(self.confidence_level, CONFIDENCE_LEVELS['medium'])
        return f"{info['icon']} {info['label']}"

    @property
    def source_tag_display(self):
        for tag, icon, _, _ in SOURCE_TAGS:
            if tag == self.source_tag:
                return f"{icon} {tag}"
        return self.source_tag

    def to_dict(self):
        return {
            'id': self.id,
            'label': self.label,
            'division_id': self.division_id,
            'category': self.category,
            'area': self.area,
            'perimeter': self.perimeter,
            'length': self.length,
            'width': self.width,
            'height': self.height,
            'count': self.count,
            'unit': self.unit,
            'source_tag': self.source_tag,
            'source_tag_display': self.source_tag_display,
            'confidence_level': self.confidence_level,
            'confidence_display': self.confidence_display,
            'position_desc': self.position_desc,
            'formula_text': self.formula_text,
            'project_features': self.project_features,
            'material_code': self.material_code,
            'base_layer': self.base_layer,
            'remarks': self.remarks,
        }


# ── Report ──

class Report(db.Model):
    __tablename__ = 'reports'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128))
    format = db.Column(db.String(16), default='html')
    file_path = db.Column(db.String(256))
    summary_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)


# ── Historical Project (历史同类清单参照) ──

class HistoricalProject(db.Model):
    """历史同类项目清单，用于补漏项和合理性校验。"""
    __tablename__ = 'historical_projects'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    project_type = db.Column(db.String(32))
    file_path = db.Column(db.String(256))
    item_count = db.Column(db.Integer)
    summary_json = db.Column(db.Text)  # Stores division-by-division stats
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)


# ── Knowledge Base Entry (知识库条目) ──

class KnowledgeBaseEntry(db.Model):
    """算量知识库 — 记录临时规则、踩坑点和注意事项。

    对应 AI工程量计算注意事项-知识库.docx 的结构化版本。
    稳定规则应合并入 Project Instructions，本表保留历史记录。
    """
    __tablename__ = 'knowledge_base_entries'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128), nullable=False)
    category = db.Column(db.String(32), default='rule')  # rule, pitfall, edge_case
    scenario = db.Column(db.Text)       # 应用场景
    description = db.Column(db.Text, nullable=False)  # 规则描述
    source = db.Column(db.String(256))  # 来源（项目/图纸/经验）
    status = db.Column(db.String(16), default='active')  # active, archived, draft
    merged_to = db.Column(db.String(64))  # 并入v3哪个章节
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)


# ── Self Check Checklist (自检清单) ──

class SelfCheckItem(db.Model):
    __tablename__ = 'self_check_items'

    id = db.Column(db.Integer, primary_key=True)
    check_text = db.Column(db.String(256), nullable=False)
    is_checked = db.Column(db.Boolean, default=False)
    version_added = db.Column(db.String(8), default='v3')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)


# ── User Loader ──

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
