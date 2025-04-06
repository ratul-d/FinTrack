import os
import signal
import sys
from flask import session
from datetime import datetime, date, timedelta
from io import BytesIO
import json
from collections import defaultdict
from dateutil.relativedelta import relativedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, make_response

from flask_migrate import Migrate
import pandas as pd
from xhtml2pdf import pisa
from apscheduler.schedulers.background import BackgroundScheduler
from extensions import db, login_manager
from urllib.parse import urlsplit
from flask_login import (
    login_user, logout_user, login_required,
    current_user
)

# Initialize Flask app and configuration
app = Flask(__name__)
app.config.from_object("config.Config")

# Initialize database and migration
db.init_app(app)
login_manager.init_app(app)

login_manager.login_view = 'login'
login_manager.login_message_category = 'warning'

migrate = Migrate(app, db)

# Import models (requires app and db to be initialized first)
from models import User,Transaction, Budget, RecurringTransaction

# --------------------
# Helper functions
# --------------------

def render_pdf(template_src, context_dict):
    """Render PDF from HTML template using xhtml2pdf."""
    html = render_template(template_src, **context_dict)
    result = BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=result)
    if pisa_status.err:
        return None
    result.seek(0)
    return result

def check_budget_alert(category):
    """Check if the total expense in a category exceeds the set budget."""
    budget = Budget.query.filter_by(user_id=current_user.id,category=category).first()
    if not budget:
        return None

    # Sum expenses (only expenses; transaction_type == 'expense') for the given category
    total_expense = db.session.query(db.func.sum(Transaction.amount)).filter_by(
        category=category, transaction_type="expense"
    ).scalar() or 0
    if total_expense > budget.limit:
        return f"You have exceeded your budget for {category}!"
    return None

def build_chart_data(transactions):
    # Spending Trends (sum of expenses per day)
    trend = defaultdict(float)
    for t in transactions:
        if t.transaction_type == "expense":
            day = t.date.strftime("%Y-%m-%d")
            trend[day] += t.amount
    # sort by date
    dates = sorted(trend.keys())
    trend_values = [trend[d] for d in dates]

    # Category-wise Spending (sum of expenses per category)
    cat = defaultdict(float)
    for t in transactions:
        if t.transaction_type == "expense":
            cat[t.category] += t.amount
    categories = list(cat.keys())
    cat_values = [cat[c] for c in categories]

    return {
        "trend_labels": dates,
        "trend_data": trend_values,
        "category_labels": categories,
        "category_data": cat_values,
    }

# --------------------
# Routes
# --------------------

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ——— Signup ———
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form['email'].lower()
        pwd = request.form['password']
        confirm_pwd = request.form['confirm_password']

        if pwd != confirm_pwd:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('signup'))

        if User.query.filter_by(email=email).first():
            flash('Account already exists.', 'warning')
            return redirect(url_for('signup'))

        u = User(email=email)
        u.set_password(pwd)
        db.session.add(u)
        db.session.commit()
        flash('Account created—please log in.', 'success')
        return redirect(url_for('login'))

    # GET request: render template with cache headers
    resp = make_response(render_template('login_signup.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

# ——— Login ———
@app.route('/login', methods=['GET','POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method=='POST':
        email = request.form['email'].lower()
        pwd   = request.form['password']
        user = User.query.filter_by(email=email).first()
        if not user:
            flash('No account found with that email.', 'danger')
            return redirect(url_for('login'))
        if not user.check_password(pwd):
            flash('Invalid password.', 'danger')
            return redirect(url_for('login'))
        login_user(user, remember=True)
        next_page = request.args.get('next')
        # security: ensure next is safe
        if not next_page or urlsplit(next_page).netloc != '':
            next_page = url_for('dashboard')
        return redirect(next_page)
    resp = make_response(render_template('login_signup.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

# ——— Logout ———
@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# ——— Dashboard ———
@app.route('/dashboard')
@login_required
def dashboard():
    # 1) Fetch only this user's data
    transactions = (
        Transaction.query
        .filter_by(user_id=current_user.id)
        .order_by(Transaction.date.desc())
        .all()
    )
    budgets = Budget.query.filter_by(user_id=current_user.id).all()

    # 2) Check budget alerts
    alerts = []
    for b in budgets:
        total_expense = (
            db.session.query(db.func.sum(Transaction.amount))
            .filter_by(
                user_id=current_user.id,
                category=b.category,
                transaction_type='expense'
            )
            .scalar()
            or 0
        )
        if total_expense > b.limit:
            alerts.append(f"You have exceeded your budget for {b.category}!")

    # 3) Chart data
    charts = build_chart_data(transactions)

    # 4) Income / expenses / savings
    total_income   = sum(t.amount for t in transactions if t.transaction_type == 'income')
    total_expenses = sum(t.amount for t in transactions if t.transaction_type == 'expense')
    net_savings    = total_income - total_expenses

    # 5) Attach recurring‐frequency labels
    for t in transactions:
        if t.recurring:
            rec = RecurringTransaction.query.filter_by(
                user_id=current_user.id,
                transaction_type=t.transaction_type,
                category=t.category,
                amount=t.amount
            ).first()
            t.frequency = rec.frequency if rec else None
        else:
            t.frequency = None

    # 6) Render with no-cache headers
    resp = make_response(render_template(
        'dashboard.html',
        transactions=transactions,
        budgets=budgets,
        alerts=alerts,
        is_search=False,
        search_results=None,
        net_income=total_income,
        net_expenses=total_expenses,
        net_savings=net_savings,
        trend_labels=json.dumps(charts['trend_labels']),
        trend_data=json.dumps(charts['trend_data']),
        category_labels=json.dumps(charts['category_labels']),
        category_data=json.dumps(charts['category_data']),
        default_date=date.today().strftime('%Y-%m-%d')
    ))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('home.html')

@app.route("/add_transaction", methods=["POST"])
@login_required
def add_transaction():
    transaction_type = request.form.get("transaction_type")
    category         = request.form.get("category")
    amount           = request.form.get("amount")
    description      = request.form.get("description")
    date_str         = request.form.get("date")
    recurring        = True if request.form.get("recurring") == "on" else False

    # parse amount
    try:
        amount = float(amount)
    except ValueError:
        flash("Invalid amount provided.", "danger")
        return redirect(url_for("index"))

    # parse date
    try:
        txn_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        flash("Invalid date provided.", "danger")
        return redirect(url_for("index"))

    new_txn = Transaction(
        user_id=current_user.id,
        transaction_type=transaction_type,
        category=category,
        amount=amount,
        description=description,
        recurring=recurring,
        date=txn_date
    )
    db.session.add(new_txn)
    if recurring:
        freq = request.form.get("frequency")  # "daily", "weekly", "monthly"
        next_run = txn_date  # first run = today’s date
        rec = RecurringTransaction(
            user_id=current_user.id,
            transaction_type=transaction_type,
            category=category,
            frequency=freq,
            amount=amount,
            next_run=next_run
        )
        db.session.add(rec)
    db.session.commit()

    flash("Transaction added successfully.", "success")
    return redirect(url_for("index"))


@app.route("/delete_transaction/<int:id>", methods=["POST"])
@login_required
def delete_transaction(id):
    transaction = Transaction.query.get_or_404(id)
    db.session.delete(transaction)
    db.session.commit()
    flash("Transaction deleted successfully.", "success")
    return redirect(url_for("index"))

@app.route("/delete_budget/<int:id>", methods=["POST"])
@login_required
def delete_budget(id):
    budget = Budget.query.get_or_404(id)
    db.session.delete(budget)
    db.session.commit()
    flash("Budget deleted successfully.", "success")
    return redirect(url_for("index"))

@app.route("/set_budget", methods=["POST"])
@login_required
def set_budget():
    category = request.form.get("budget_category")
    budget_limit = request.form.get("budget_limit")

    try:
        budget_limit = float(budget_limit)
    except ValueError:
        flash("Invalid budget limit.", "danger")
        return redirect(url_for("index"))

    budget = Budget.query.filter_by(user_id=current_user.id, category=category).first()
    if budget:
        budget.limit = budget_limit
    else:
        budget = Budget(user_id=current_user.id,category=category, limit=budget_limit)
        db.session.add(budget)
    db.session.commit()
    flash("Budget set successfully.", "success")
    return redirect(url_for("index"))

@app.route("/search", methods=["GET"])
@login_required
def search():
    search_category = request.args.get("search_category")
    search_type = request.args.get("search_type")
    min_amount = request.args.get("min_amount")
    max_amount = request.args.get("max_amount")

    # Build query
    query = Transaction.query.filter_by(user_id=current_user.id)

    if search_category:
        query = query.filter(Transaction.category.ilike(f"%{search_category}%"))

    if search_type in ['income', 'expense']:
        query = query.filter(Transaction.transaction_type == search_type)

    if min_amount:
        try:
            query = query.filter(Transaction.amount >= float(min_amount))
        except ValueError:
            pass

    if max_amount:
        try:
            query = query.filter(Transaction.amount <= float(max_amount))
        except ValueError:
            pass

    search_results = query.order_by(Transaction.date.desc()).all()
    main_transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    charts = build_chart_data(main_transactions)
    budgets = Budget.query.filter_by(user_id=current_user.id).all()
    alerts = []
    for budget in budgets:
        alert = check_budget_alert(budget.category)
        if alert:
            alerts.append(alert)

    total_income = sum(t.amount for t in main_transactions if t.transaction_type == "income")
    total_expenses = sum(t.amount for t in main_transactions if t.transaction_type == "expense")
    net_savings = total_income - total_expenses

    for t in main_transactions:
        if t.recurring:
            rec = RecurringTransaction.query.filter_by(
                transaction_type=t.transaction_type,
                category=t.category,
                amount=t.amount
            ).first()
            t.frequency = rec.frequency if rec else None
        else:
            t.frequency = None

    return render_template("dashboard.html",
                           transactions=main_transactions,
                           search_results=search_results,
                           budgets=budgets,
                           alerts=alerts,
                           is_search=True,
                           net_income=total_income,
                           net_expenses=total_expenses,
                           net_savings=net_savings,
                           trend_labels=json.dumps(charts["trend_labels"]),
                           trend_data=json.dumps(charts["trend_data"]),
                           category_labels=json.dumps(charts["category_labels"]),
                           category_data=json.dumps(charts["category_data"]),
                           default_date=date.today().strftime("%Y-%m-%d"))

@app.route("/export/csv")
@login_required
def export_csv():
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    # Creates DataFrame with Frequency
    data = []
    for t in transactions:
        frequency = ""
        if t.recurring:
            # Finds matching recurring transaction
            rec = RecurringTransaction.query.filter_by(
                transaction_type=t.transaction_type,
                category=t.category,
                amount=t.amount
            ).first()
            if rec:
                frequency = rec.frequency
        data.append({
            "Date": t.date.strftime("%Y-%m-%d"),
            "Type": t.transaction_type,
            "Category": t.category,
            "Amount": t.amount,
            "Description": t.description,
            "Recurring": t.recurring,
            "Frequency": frequency
        })
    df = pd.DataFrame(data)
    # Create CSV in memory
    csv_data = df.to_csv(index=False)
    return send_file(
        BytesIO(csv_data.encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="transactions.csv"
    )



import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import base64
from io import BytesIO


@app.route("/export/pdf")
@login_required
def export_pdf():
    # Get data
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    budgets = Budget.query.filter_by(user_id=current_user.id).all()

    for txn in transactions:
        if txn.recurring:
            rec = RecurringTransaction.query.filter_by(
                transaction_type=txn.transaction_type,
                category=txn.category,
                amount=txn.amount
            ).first()
            txn.frequency = rec.frequency if rec else 'Recurring'
        else:
            txn.frequency = None

    # Calculate totals
    total_income = sum(t.amount for t in transactions if t.transaction_type == "income")
    total_expenses = sum(t.amount for t in transactions if t.transaction_type == "expense")
    net_savings = total_income - total_expenses

    # Generate chart data
    charts = build_chart_data(transactions)

    # Create trend chart
    trend_buffer = BytesIO()
    fig1 = plt.figure(figsize=(10, 5))
    plt.plot(charts['trend_labels'], charts['trend_data'], marker='o', linestyle='-')
    plt.title('Spending Trend Over Time')
    plt.xlabel('Date')
    plt.ylabel('Amount ($)')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    plt.savefig(trend_buffer, format='png')
    plt.close(fig1)
    trend_image = base64.b64encode(trend_buffer.getvalue()).decode()

    # Create category chart
    category_buffer = BytesIO()
    fig2 = plt.figure(figsize=(10, 5))
    plt.bar(charts['category_labels'], charts['category_data'])
    plt.title('Spending by Category')
    plt.xlabel('Category')
    plt.ylabel('Amount ($)')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    plt.savefig(category_buffer, format='png')
    plt.close(fig2)
    category_image = base64.b64encode(category_buffer.getvalue()).decode()

    # Build context
    context = {
        "transactions": transactions,
        "budgets": budgets,
        "export_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "net_savings": net_savings,
        "net_income": total_income,
        "net_expenses": total_expenses,
        "trend_image": trend_image,
        "category_image": category_image
    }

    # Generate PDF
    pdf = render_pdf("pdf_template.html", context)
    return send_file(
        pdf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="financial_report.pdf"
    ) if pdf else redirect(url_for("index"))

from apscheduler.schedulers.background import BackgroundScheduler


def run_recurring_jobs():
    with app.app_context():
        today = date.today()
        recs = RecurringTransaction.query.filter(RecurringTransaction.next_run <= today).all()

        for rec in recs:
            txn = Transaction(
                user_id=rec.user_id,
                transaction_type=rec.transaction_type,
                category=rec.category,
                amount=rec.amount,
                description=f"Auto-generated {rec.frequency} recurring",
                recurring=True,
                date=rec.next_run
            )
            db.session.add(txn)

            # Update next_run based on frequency
            if rec.frequency == "daily":
                rec.next_run += timedelta(days=1)
            elif rec.frequency == "weekly":
                rec.next_run += timedelta(weeks=1)
            elif rec.frequency == "monthly":
                rec.next_run += relativedelta(months=1)

        db.session.commit()


def handle_shutdown(signum, frame):
    """Clear all sessions and logout users before shutdown"""
    with app.app_context():
        # Invalidate server-side session data
        session.clear()
        # Clear all remember-me cookies at database level
        User.query.update({'password': None})  # Invalidates all auth tokens
        db.session.commit()

    print("\n\033[91mServer shutting down - All users logged out\033[0m")
    sys.exit(0)
signal.signal(signal.SIGINT, handle_shutdown)  # CTRL+C
signal.signal(signal.SIGTERM, handle_shutdown)

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_recurring_jobs, 'cron', hour=0, minute=1)
    scheduler.start()

    try:
        app.run(debug=True)
    except KeyboardInterrupt:
        scheduler.shutdown()
        plt.close('all')
