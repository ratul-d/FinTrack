from datetime import datetime, date
from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

class User(db.Model, UserMixin):
    id       = db.Column(db.Integer, primary_key=True)
    email    = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    # relationships
    transactions = db.relationship('Transaction', backref='user', lazy=True)
    budgets      = db.relationship('Budget', backref='user', lazy=True)
    recurrences  = db.relationship('RecurringTransaction', backref='user', lazy=True)

    def set_password(self, raw):
        self.password = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password, raw)

class Transaction(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date             = db.Column(db.Date, default=date.today)
    transaction_type = db.Column(db.String(10), nullable=False)
    category         = db.Column(db.String(50), nullable=False)
    amount           = db.Column(db.Float, nullable=False)
    description      = db.Column(db.String(200))
    recurring        = db.Column(db.Boolean, default=False)

class Budget(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    user_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    limit    = db.Column(db.Float, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id','category', name='uix_user_category'),
    )

class RecurringTransaction(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    transaction_type = db.Column(db.String(10), nullable=False)
    category         = db.Column(db.String(50), nullable=False)
    frequency        = db.Column(db.String(10), nullable=False)
    amount           = db.Column(db.Float, nullable=False)
    next_run         = db.Column(db.Date, nullable=False, default=date.today)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
