from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    company = db.Column(db.String(128))
    phone = db.Column(db.String(20))
    is_admin = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)  # Admin must approve new users
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    projects = db.relationship('Project', backref='owner', lazy='dynamic',
                               cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


class Project(db.Model):
    __tablename__ = 'projects'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text)
    address = db.Column(db.String(256))
    drawing_type = db.Column(db.String(32), default='floor_plan')  # floor_plan, elevation, section
    scale = db.Column(db.Float, default=1.0)  # pixels per meter (after calibration)
    scale_reference = db.Column(db.String(64))  # reference dimension for scale calibration
    status = db.Column(db.String(20), default='pending')  # pending, processing, completed, failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    drawings = db.relationship('Drawing', backref='project', lazy='dynamic',
                               cascade='all, delete-orphan')
    measurements = db.relationship('Measurement', backref='project', lazy='dynamic',
                                   cascade='all, delete-orphan')
    report = db.relationship('Report', backref='project', uselist=False,
                             cascade='all, delete-orphan')


class Drawing(db.Model):
    __tablename__ = 'drawings'

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(256), nullable=False)
    original_name = db.Column(db.String(256), nullable=False)
    file_type = db.Column(db.String(10), nullable=False)  # png, jpg, dxf, etc.
    file_size = db.Column(db.Integer)  # bytes
    width = db.Column(db.Integer)  # image width in pixels
    height = db.Column(db.Integer)  # image height in pixels
    annotated_path = db.Column(db.String(256))  # path to annotated image
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)


class Measurement(db.Model):
    __tablename__ = 'measurements'

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(128))  # e.g. "客厅", "主卧"
    category = db.Column(db.String(32), nullable=False)  # room, wall, door, window, fixture
    area = db.Column(db.Float)  # square meters
    perimeter = db.Column(db.Float)  # meters
    length = db.Column(db.Float)  # meters
    width = db.Column(db.Float)  # meters
    height = db.Column(db.Float)  # meters
    count = db.Column(db.Integer)  # count for fixtures/items
    unit = db.Column(db.String(16), default='m²')
    data_json = db.Column(db.Text)  # JSON for additional measurement data
    confidence = db.Column(db.Float)  # detection confidence 0-1
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    drawing_id = db.Column(db.Integer, db.ForeignKey('drawings.id'), nullable=True)

    drawing = db.relationship('Drawing', backref='measurements')


class Report(db.Model):
    __tablename__ = 'reports'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128))
    format = db.Column(db.String(16), default='html')  # html, pdf
    file_path = db.Column(db.String(256))
    summary_json = db.Column(db.Text)  # JSON summary for quick display
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
