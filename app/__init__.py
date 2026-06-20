from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录以访问此页面。'
login_manager.login_message_category = 'warning'


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Ensure upload and reports directories exist
    import os
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['REPORTS_FOLDER'], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from app.auth import auth_bp
    from app.routes import main_bp
    from app.api import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')

    with app.app_context():
        from app import models
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
            admin.set_password(app.config['ADMIN_PASSWORD'])
            db.session.add(admin)
            db.session.commit()

        # Seed initial knowledge base entries (first run)
        if not KnowledgeBaseEntry.query.first():
            db.session.add_all([
                KnowledgeBaseEntry(
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
                KnowledgeBaseEntry(
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
            ])
            db.session.commit()

    return app
