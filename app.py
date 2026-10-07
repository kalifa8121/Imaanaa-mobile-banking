import os
import sqlite3
import subprocess
import datetime
import random
import shutil
import sys
import time
import atexit
from io import BytesIO

import psycopg2
from psycopg2 import OperationalError as PostgreSQLOperationalError
from psycopg2.extras import DictCursor

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from flask import (
    Flask, request, redirect, url_for, session, 
    render_template_string, send_from_directory, jsonify, send_file
)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "imana_free_interest_microfinance_secret_key_2026")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
BACKUP_FOLDER = os.path.join(BASE_DIR, 'backups')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['BACKUP_FOLDER'] = BACKUP_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)

NOTIFICATIONS = []

ETHIOPIAN_BANKS = [
    "Commercial Bank of Ethiopia (CBE)",
    "Cooperative Bank of Oromia (CBO)",
    "Awash Bank",
    "Dashen Bank",
    "Bank of Abyssinia",
    "Hibret Bank",
    "Nib International Bank",
    "United Bank",
    "Zemen Bank",
    "Wegagen Bank",
    "Oromia Bank",
    "Amhara Bank",
    "Global Bank Ethiopia",
    "Hijra Bank (Islamic)",
    "ZamZam Bank (Islamic)",
    "Siinqee Bank",
    "Gadaa Bank"
]

def compress_and_save_image(file_storage, target_filename, max_size=(300, 300), quality=35):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], target_filename)
    filename = file_storage.filename.lower()
    
    if filename.endswith('.pdf') or not HAS_PIL:
        file_storage.save(filepath)
        return target_filename

    try:
        image = Image.open(file_storage)
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
        
        image.thumbnail(max_size, Image.Resampling.LANCZOS)
        image.save(filepath, "JPEG", optimize=True, quality=quality)
        return target_filename
    except Exception as e:
        print(f"Image compression error: {e}")
        file_storage.save(filepath)
        return target_filename

def _translate_sql_placeholders(sql):
    return sql.replace("?", "%s")

class CompatiblePostgresCursor(DictCursor):
    def execute(self, query, vars=None):
        return super().execute(_translate_sql_placeholders(query), vars)

    def executemany(self, query, vars_list):
        return super().executemany(_translate_sql_placeholders(query), vars_list)

def get_db_connection(max_retries=10, delay=0.5):
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(
                database_url,
                connect_timeout=15,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
                cursor_factory=CompatiblePostgresCursor,
            )
            return conn
        except PostgreSQLOperationalError as e:
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                raise e

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_commission(amount):
    if 1000 <= amount <= 3000:
        return 50.0
    elif 3001 <= amount <= 5000:
        return 80.0
    elif 5001 <= amount <= 10000:
        return 100.0
    elif 10001 <= amount <= 20000:
        return 200.0
    elif 20001 <= amount <= 40000:
        return 400.0
    elif amount > 40001:
        return 500.0
    return 0.0

def add_notification(message):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    NOTIFICATIONS.insert(0, f"[{now}] {message}")
    if len(NOTIFICATIONS) > 20:
        NOTIFICATIONS.pop()

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT DEFAULT 'ACTIVE'
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        default_users = [
            ('ceo', 'ceo999', 'CEO', 'ACTIVE'),
            ('manager1', 'manager123', 'MANAGER', 'ACTIVE'),
            ('maker1', 'maker123', 'MAKER', 'ACTIVE'),
            ('auditor1', 'auditor123', 'AUDITOR', 'ACTIVE'),
            ('officer1', 'officer123', 'LOAN_OFFICER', 'ACTIVE'),
            ('ext_agent1', 'agent123', 'EXTERNAL_AGENT', 'ACTIVE')
        ]
        cursor.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", default_users)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            gender TEXT DEFAULT 'Dhiira',
            account_type TEXT DEFAULT 'WADIA',
            photo_path TEXT,
            signature_path TEXT,
            national_id_path TEXT DEFAULT '',
            balance REAL DEFAULT 0.0,
            status TEXT DEFAULT 'PENDING_APPROVAL',
            freeze_status TEXT DEFAULT 'UNFROZEN',
            freeze_reason TEXT DEFAULT '',
            mobile_pin TEXT DEFAULT '1234',
            mobile_status TEXT DEFAULT 'INACTIVE',
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            txn_id TEXT PRIMARY KEY,
            txn_type TEXT,
            customer_id TEXT,
            customer_name TEXT,
            target_account TEXT,
            amount REAL,
            commission REAL DEFAULT 0.0,
            bank_name TEXT,
            ft_reference TEXT,
            status TEXT DEFAULT 'PENDING_MANAGER',
            created_by TEXT,
            timestamp TEXT,
            audited_status TEXT DEFAULT 'OPEN',
            reason TEXT DEFAULT ''
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reversals (
            reversal_id TEXT PRIMARY KEY,
            txn_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            manager_approved INTEGER DEFAULT 0,
            ceo_approved INTEGER DEFAULT 0,
            status TEXT DEFAULT 'PENDING_APPROVAL',
            timestamp TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS islamic_financing (
            loan_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            customer_name TEXT,
            financing_type TEXT NOT NULL,
            principal_amount REAL NOT NULL,
            profit_margin REAL DEFAULT 0.0,
            total_repayment REAL NOT NULL,
            tenure_months INTEGER,
            monthly_installment REAL,
            status TEXT DEFAULT 'PENDING_MANAGER',
            manager_approved INTEGER DEFAULT 0,
            ceo_approved INTEGER DEFAULT 0,
            agent_notes TEXT,
            created_by TEXT,
            timestamp TEXT
        )
    """)

    conn.commit()
    conn.close()

try:
    init_db()
except Exception as e:
    print(f"⚠️ [DB INIT WARNING]: {e}")

def get_bank_capital():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE status='APPROVED' AND txn_type IN ('DEPOSIT', 'MOBILE_TOPUP_REC')")
    total_deposit = cursor.fetchone()[0] or 0.0
    
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE status='APPROVED' AND txn_type IN ('WITHDRAWAL', 'T24_TRANSFER', 'RTGS_TRANSFER', 'WALLET_TOPUP', 'AIRTIME_TOPUP')")
    total_withdraw = cursor.fetchone()[0] or 0.0
    
    cursor.execute("SELECT SUM(balance) FROM customers WHERE status='ACTIVE'")
    total_cust_balance = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT SUM(commission) FROM transactions WHERE status='APPROVED'")
    total_commission = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT SUM(balance) FROM customers WHERE status='ACTIVE' AND account_type='MUDARABA'")
    total_mudaraba_deposits = cursor.fetchone()[0] or 0.0

    mudaraba_gross_profit = total_mudaraba_deposits * 0.10
    mudaraba_ceo_share = mudaraba_gross_profit * 0.50
    mudaraba_customer_share = mudaraba_gross_profit * 0.50
    
    net_capital = total_deposit - total_withdraw + total_commission
    conn.close()
    return max(0.0, net_capital), total_deposit, total_withdraw, total_cust_balance, total_commission, total_mudaraba_deposits, mudaraba_gross_profit, mudaraba_ceo_share, mudaraba_customer_share

HTML_LAYOUT = """
<!DOCTYPE html>
<html lang="om">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Imana Free Interest Microfinance</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background-color: #f1f5f9; padding-bottom: 80px; color: #0f172a; }
        nav { background: linear-gradient(135deg, #065f46, #047857); color: white; padding: 12px 16px; position: sticky; top: 0; z-index: 50; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
        .logo-container { display: flex; align-items: center; gap: 10px; }
        .logo-svg { width: 32px; height: 32px; fill: #fbbf24; }
        nav h1 { font-size: 15px; font-weight: 800; letter-spacing: 0.3px; color: #ffffff; }
        .role-badge { background: #0284c7; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 11px; }
        .container { max-width: 600px; margin: 0 auto; padding: 16px; }
        .notification-bar { background: #fef3c7; color: #92400e; padding: 8px 12px; border-radius: 8px; font-size: 11px; margin-bottom: 12px; font-weight: bold; border: 1px solid #fde68a; }
        .card-net { background: linear-gradient(135deg, #064e3b, #047857); color: white; border-radius: 16px; padding: 20px; box-shadow: 0 10px 15px -3px rgba(6,78,59,0.3); margin-bottom: 20px; }
        .card-ceo-profit { background: linear-gradient(135deg, #4c1d95, #6b21a8); color: white; border-radius: 16px; padding: 20px; box-shadow: 0 10px 15px -3px rgba(76,29,149,0.3); margin-bottom: 20px; }
        .card-mobile-header { background: linear-gradient(135deg, #1e3a8a, #2563eb); color: white; border-radius: 16px; padding: 20px; box-shadow: 0 10px 15px -3px rgba(37,99,235,0.3); margin-bottom: 20px; }
        .net-title { font-size: 12px; opacity: 0.9; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
        .net-amount { font-size: 30px; font-weight: 800; color: #fbbf24; }
        .net-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 16px; border-top: 1px solid rgba(255,255,255,0.2); font-size: 12px; padding-top: 12px; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .btn-card { background: white; padding: 16px; border-radius: 12px; border: 1px solid #e2e8f0; display: flex; flex-direction: column; align-items: center; text-decoration: none; color: #334155; font-weight: bold; font-size: 13px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.05); transition: 0.2s; }
        .btn-card:active { transform: scale(0.97); }
        .btn-card span.icon { font-size: 26px; margin-bottom: 8px; }
        .btn-card-ceo { background: #faf5ff; border-color: #e9d5ff; color: #581c87; }
        .btn-card-auditor { background: #fff7ed; border-color: #ffedd5; color: #c2410c; }
        .btn-card-mobile { background: #eff6ff; border-color: #bfdbfe; color: #1d4ed8; }
        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: white; border-top: 1px solid #e2e8f0; display: flex; justify-content: space-around; padding: 10px 0; z-index: 50; }
        .bottom-nav a { text-align: center; color: #64748b; text-decoration: none; font-size: 11px; flex: 1; font-weight: 500; }
        .bottom-nav a span.icon { display: block; font-size: 18px; margin-bottom: 2px; }
        .box { background: white; padding: 20px; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-bottom: 16px; }
        .form-group { margin-bottom: 12px; position: relative; }
        .form-group label { display: block; font-size: 12px; font-weight: bold; color: #475569; margin-bottom: 4px; }
        .input-field { width: 100%; padding: 11px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 14px; outline: none; }
        .input-field:focus { border-color: #047857; }
        .btn-submit { width: 100%; background: #047857; color: white; border: none; padding: 12px; border-radius: 8px; font-weight: bold; font-size: 14px; cursor: pointer; }
        .badge { padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; display: inline-block; }
        .badge-pending { background: #fef3c7; color: #92400e; }
        .badge-active { background: #dcfce7; color: #166534; }
        .badge-danger { background: #fee2e2; color: #991b1b; }
        .btn-action { padding: 8px 14px; border-radius: 6px; color: white; text-decoration: none; font-size: 12px; font-weight: bold; display: inline-block; border:none; cursor:pointer; }
        .btn-blue { background: #2563eb; }
        .btn-green { background: #16a34a; }
        .btn-red { background: #dc2626; }
        .btn-purple { background: #7c3aed; }
        .pwd-toggle { position: absolute; right: 10px; top: 32px; cursor: pointer; user-select: none; font-size: 14px; }
        @media print {
            .bottom-nav, nav, .no-print { display: none !important; }
            body { padding-bottom: 0; background: white; }
            .box { border: none; box-shadow: none; }
        }
    </style>
</head>
<body>
    <nav class="no-print">
        <div class="logo-container">
            <svg class="logo-svg" viewBox="0 0 24 24">
                <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
            </svg>
            <h1>Imana Free Interest Microfinance</h1>
        </div>
        {% if session.get('role') %}
            <div style="font-size:12px;">
                <span style="margin-right:4px;"><b>{{ session['username'] }}</b></span>
                <span class="role-badge">{{ session['role'] }}</span>
                <a href="/change_password" style="color: #fde047; margin-left:8px; text-decoration:none;">🔑 Password</a>
                <a href="/logout" style="color: #fca5a5; margin-left:8px; text-decoration:none;">Logout</a>
            </div>
        {% elif session.get('mobile_cust_id') %}
            <div style="font-size:12px;">
                <span style="margin-right:4px;">📱 <b>{{ session['mobile_name'] }}</b></span>
                <a href="/mobile_change_pin" style="color: #fde047; margin-left:8px; text-decoration:none;">🔑 PIN Jijjiiri</a>
                <a href="/mobile_logout" style="color: #fca5a5; margin-left:8px; text-decoration:none;">Logout</a>
            </div>
        {% else %}
            <a href="/mobile_login" style="color: #fbbf24; font-size: 12px; font-weight: bold; text-decoration: none;">📱 Mobile Banking</a>
        {% endif %}
    </nav>

    <div class="container">
        {% if notifications %}
            <div class="notification-bar no-print">
                🔔 NOTIFICATION: {{ notifications[0] }}
            </div>
        {% endif %}
        {% block content %}{% endblock %}
    </div>

    {% if session.get('role') %}
    <div class="bottom-nav no-print">
        <a href="/"><span class="icon">🏠</span>Dashboard</a>
        {% if session['role'] == 'MAKER' %}
            <a href="/register"><span class="icon">👤</span>Galmee</a>
            <a href="/transaction"><span class="icon">💸</span>Kaffaltii</a>
        {% endif %}
        {% if session['role'] in ['MANAGER', 'CEO'] %}
            <a href="/reversals_list"><span class="icon">🔄</span>Reversals</a>
        {% endif %}
    </div>
    {% elif session.get('mobile_cust_id') %}
    <div class="bottom-nav no-print">
        <a href="/mobile_dashboard"><span class="icon">📱</span>Dasshbordii</a>
        <a href="/mobile_transfer"><span class="icon">🔄</span>Transfer</a>
        <a href="/mobile_statement"><span class="icon">📜</span>Statement</a>
    </div>
    {% endif %}
</body>
</html>
"""

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT username, role, status FROM users WHERE username = ? AND password = ?", (username, password))
        user = cursor.fetchone()
        conn.close()

        if user:
            if user['status'] == 'BLOCKED':
                error = "🚫 Akkaawunttii keessan UGGURAMEERA!"
            else:
                session.clear()
                session['username'] = user['username']
                session['role'] = user['role']
                return redirect('/')
        else:
            error = "Username ykn Password dogoggoraa!"

    err_html = f"<p style='color:red; font-size:12px; text-align:center;'>{error}</p>" if error else ""
    content = f"""
    <div class="box" style="margin-top: 30px; text-align: center;">
        <h2>🏦 Staff Login</h2>
        {err_html}
        <form method="POST">
            <div class="form-group" style="text-align:left;">
                <label>Username</label>
                <input type="text" name="username" class="input-field" required>
            </div>
            <div class="form-group" style="text-align:left;">
                <label>Password</label>
                <input type="password" name="password" class="input-field" required>
            </div>
            <button type="submit" class="btn-submit">Seeni</button>
        </form>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content), notifications=NOTIFICATIONS)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# --- MOBILE BANKING ROUTES ---

@app.route('/mobile_login', methods=['GET', 'POST'])
def mobile_login():
    msg = None
    if request.method == 'POST':
        cust_id = request.form.get('customer_id', '').strip()
        pin = request.form.get('mobile_pin', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT customer_id, full_name, phone, balance, status, freeze_status, mobile_pin, mobile_status FROM customers WHERE customer_id = ?", (cust_id,))
        cust = cursor.fetchone()
        conn.close()

        if cust:
            if cust['status'] != 'ACTIVE':
                msg = "❌ Akkaawunttiin keessan active miti."
            elif cust['freeze_status'] == 'FROZEN':
                msg = "🚫 Akkaawunttiin keessan uggurameera (Frozen)."
            elif cust['mobile_pin'] != pin:
                msg = "❌ PIN Mobile Banking dogoggoraa!"
            else:
                session.clear()
                session['mobile_cust_id'] = cust['customer_id']
                session['mobile_name'] = cust['full_name']
                session['mobile_phone'] = cust['phone']
                return redirect('/mobile_dashboard')
        else:
            msg = "❌ Lakkoofsa Akkaawuntii argachuu hin dandeenye!"

    content = f"""
    <div class="box" style="margin-top:20px; text-align:center;">
        <h2>📱 Mobile Banking Login</h2>
        {f"<p style='color:red;'>{msg}</p>" if msg else ""}
        <form method="POST">
            <div class="form-group" style="text-align:left;">
                <label>Lakkoofsa Akkaawuntii</label>
                <input type="text" name="customer_id" class="input-field" required>
            </div>
            <div class="form-group" style="text-align:left;">
                <label>PIN (Default: 1234)</label>
                <input type="password" name="mobile_pin" maxlength="6" class="input-field" required>
            </div>
            <button type="submit" class="btn-submit">Seeni Mobile Banking</button>
        </form>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content), notifications=NOTIFICATIONS)

@app.route('/mobile_logout')
def mobile_logout():
    session.clear()
    return redirect('/mobile_login')

@app.route('/mobile_dashboard')
def mobile_dashboard():
    if 'mobile_cust_id' not in session:
        return redirect('/mobile_login')

    cust_id = session['mobile_cust_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT customer_id, full_name, phone, balance, account_type FROM customers WHERE customer_id = ?", (cust_id,))
    cust = cursor.fetchone()

    # NOTE: Status != 'REVERSED' to hide reversed transactions from customer
    cursor.execute("""
        SELECT txn_id, txn_type, amount, bank_name, status, timestamp, ft_reference 
        FROM transactions 
        WHERE customer_id = ? AND status != 'REVERSED' 
        ORDER BY timestamp DESC LIMIT 5
    """, (cust_id,))
    recent_txns = cursor.fetchall()
    conn.close()

    rows_html = ""
    for t in recent_txns:
        color = "#16a34a" if t['txn_type'] in ['DEPOSIT', 'MOBILE_TOPUP_REC'] else "#dc2626"
        sign = "+" if t['txn_type'] in ['DEPOSIT', 'MOBILE_TOPUP_REC'] else "-"
        rows_html += f"""
        <div style="display:flex; justify-content:space-between; align-items:center; padding:10px 0; border-bottom:1px solid #f1f5f9; font-size:12px;">
            <div>
                <div style="font-weight:bold; color:#1e293b;">{t['txn_type']}</div>
                <div style="font-size:10px; color:#64748b;">{t['timestamp']} | Ref: {t['ft_reference']}</div>
            </div>
            <div style="text-align:right;">
                <div style="font-weight:800; color:{color};">{sign}{t['amount']:,.2f} Birr</div>
                <span class="badge badge-active" style="font-size:9px;">{t['status']}</span>
            </div>
        </div>
        """

    content = f"""
    <div class="card-mobile-header">
        <div class="net-title">Baga Nagaan Dhuftan 👋</div>
        <div style="font-size:20px; font-weight:bold;">{cust['full_name']}</div>
        <div style="font-size:11px; opacity:0.9;">Acc: <b>{cust['customer_id']}</b> ({cust['account_type']})</div>
        <div style="margin-top:16px; border-top:1px solid rgba(255,255,255,0.2); padding-top:12px;">
            <div class="net-title">Haafeeka Herregaa (Current Balance)</div>
            <div class="net-amount">{cust['balance']:,.2f} Birr</div>
        </div>
    </div>

    <div class="box">
        <h3>📊 Sochiilee Dhiyootti Hojjataman</h3>
        {rows_html if rows_html else '<p style="font-size:12px; color:#94a3b8;">Sochiin transaction keessan argame hin jiru.</p>'}
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content), notifications=NOTIFICATIONS)

@app.route('/mobile_statement')
def mobile_statement():
    if 'mobile_cust_id' not in session:
        return redirect('/mobile_login')

    cust_id = session['mobile_cust_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT customer_id, full_name, phone, balance, account_type FROM customers WHERE customer_id = ?", (cust_id,))
    cust = cursor.fetchone()

    # Filter out REVERSED transactions so customer never sees them
    cursor.execute("""
        SELECT txn_id, txn_type, amount, commission, bank_name, status, timestamp, ft_reference, reason 
        FROM transactions 
        WHERE customer_id = ? AND status != 'REVERSED' 
        ORDER BY timestamp DESC
    """, (cust_id,))
    txns = cursor.fetchall()
    conn.close()

    rows_html = ""
    for t in txns:
        color = "#16a34a" if t['txn_type'] in ['DEPOSIT', 'MOBILE_TOPUP_REC'] else "#dc2626"
        sign = "+" if t['txn_type'] in ['DEPOSIT', 'MOBILE_TOPUP_REC'] else "-"
        rows_html += f"""
        <tr style="border-bottom:1px solid #e2e8f0; font-size:12px;">
            <td style="padding:8px;">{t['timestamp']}</td>
            <td style="padding:8px; font-weight:bold;">{t['ft_reference']}</td>
            <td style="padding:8px;">{t['txn_type']}</td>
            <td style="padding:8px; font-weight:bold; color:{color};">{sign}{t['amount']:,.2f} Birr</td>
            <td style="padding:8px;"><span class="badge badge-active">{t['status']}</span></td>
        </tr>
        """

    content = f"""
    <div class="box">
        <h2>📜 Bank Statement</h2>
        <div style="background:#f8fafc; padding:12px; border-radius:8px; margin-bottom:16px;">
            <div>Maqaa: <b>{cust['full_name']}</b></div>
            <div>Account: <b>{cust['customer_id']}</b> ({cust['account_type']})</div>
            <div>Haafeeka Ammaa: <b style="color:#16a34a;">{cust['balance']:,.2f} Birr</b></div>
        </div>

        <table style="width:100%; border-collapse:collapse; text-align:left;">
            <thead>
                <tr style="background:#f1f5f9; font-size:11px;">
                    <th style="padding:8px;">Guyyaa</th>
                    <th style="padding:8px;">Ref</th>
                    <th style="padding:8px;">Gosa</th>
                    <th style="padding:8px;">Amount</th>
                    <th style="padding:8px;">Status</th>
                </tr>
            </thead>
            <tbody>
                {rows_html if rows_html else '<tr><td colspan="5" style="padding:16px; text-align:center;">Sochiin herregaa hin jiru.</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content), notifications=NOTIFICATIONS)

# --- TRANSACTION & REVERSAL HANDLING ---

@app.route('/transaction', methods=['GET', 'POST'])
def transaction():
    if 'role' not in session or session['role'] not in ['MAKER', 'MANAGER']:
        return "🚫 Hayyama Hin Qabdan!", 403

    msg = None
    if request.method == 'POST':
        cust_id = request.form.get('customer_id', '').strip()
        txn_type = request.form.get('txn_type')
        amount = float(request.form.get('amount', 0.0))
        target_acc = request.form.get('target_account', '').strip()
        reason = request.form.get('reason', '')

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT customer_id, full_name, balance, freeze_status FROM customers WHERE customer_id = ?", (cust_id,))
        cust = cursor.fetchone()

        if not cust:
            msg = "❌ Maammilli hin argamne!"
        elif cust['freeze_status'] == 'FROZEN':
            msg = "🚫 Akkaawunttiin maammila kanaa UGGURAMEERA!"
        elif amount <= 0:
            msg = "❌ Hammi maallaqaa ziiroo ol ta'uu qaba!"
        else:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ft_ref = f"FT{datetime.datetime.now().strftime('%y%j')}{random.randint(10000, 99999)}"
            txn_id = f"TXN-{random.randint(100000, 999999)}"

            if txn_type == 'DEPOSIT':
                cursor.execute("UPDATE customers SET balance = balance + ? WHERE customer_id = ?", (amount, cust_id))
                cursor.execute("""
                    INSERT INTO transactions (txn_id, txn_type, customer_id, customer_name, amount, commission, bank_name, ft_reference, status, created_by, timestamp, reason)
                    VALUES (?, 'DEPOSIT', ?, ?, ?, 0.0, 'Imana Microfinance', ?, 'APPROVED', ?, ?, ?)
                """, (txn_id, cust_id, cust['full_name'], amount, ft_ref, session['username'], now, reason))
                conn.commit()
                msg = f"✅ Deposit Birr {amount:,.2f} milkaa'inaan raawwatameera!"

            elif txn_type in ['WITHDRAWAL', 'T24_TRANSFER']:
                if cust['balance'] < amount:
                    msg = "❌ Haafeeka gahaa hin qabu (Insufficient Balance)!"
                else:
                    cursor.execute("UPDATE customers SET balance = balance - ? WHERE customer_id = ?", (amount, cust_id))
                    
                    if txn_type == 'T24_TRANSFER' and target_acc:
                        cursor.execute("UPDATE customers SET balance = balance + ? WHERE customer_id = ?", (amount, target_acc))

                    cursor.execute("""
                        INSERT INTO transactions (txn_id, txn_type, customer_id, customer_name, target_account, amount, commission, bank_name, ft_reference, status, created_by, timestamp, reason)
                        VALUES (?, ?, ?, ?, ?, ?, 0.0, 'Imana Microfinance', ?, 'APPROVED', ?, ?, ?)
                    """, (txn_id, txn_type, cust_id, cust['full_name'], target_acc, amount, ft_ref, session['username'], now, reason))
                    
                    conn.commit()
                    msg = f"✅ {txn_type} Birr {amount:,.2f} milkaa'inaan raawwatameera!"

        conn.close()

    content = f"""
    <div class="box">
        <h2>💸 Kaffaltii & Transfer (Deposit / Withdraw)</h2>
        {f"<p style='color:green;'>{msg}</p>" if msg else ""}
        <form method="POST">
            <div class="form-group">
                <label>Lakkoofsa Akkaawuntii Maammilaa</label>
                <input type="text" name="customer_id" class="input-field" required>
            </div>
            <div class="form-group">
                <label>Gosa Transaction</label>
                <select name="txn_type" class="input-field" required>
                    <option value="DEPOSIT">DEPOSIT (Galii)</option>
                    <option value="WITHDRAWAL">WITHDRAWAL (Baasii)</option>
                    <option value="T24_TRANSFER">TRANSFER (Dabarsaa)</option>
                </select>
            </div>
            <div class="form-group">
                <label>Target Account (Yoo Transfer Ta'e)</label>
                <input type="text" name="target_account" class="input-field">
            </div>
            <div class="form-group">
                <label>Hamma Maallaqaa (Birr)</label>
                <input type="number" step="0.01" name="amount" class="input-field" required>
            </div>
            <div class="form-group">
                <label>Sababa / Remark</label>
                <input type="text" name="reason" class="input-field">
            </div>
            <button type="submit" class="btn-submit">Raawwadhu</button>
        </form>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content), notifications=NOTIFICATIONS)

@app.route('/reversals_list', methods=['GET', 'POST'])
def reversals_list():
    if 'role' not in session or session['role'] not in ['MANAGER', 'CEO']:
        return "🚫 Hayyama Hin Qabdan!", 403

    msg = None
    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')
        rev_id = request.form.get('reversal_id')

        cursor.execute("SELECT reversal_id, txn_id, status FROM reversals WHERE reversal_id = ?", (rev_id,))
        rev = cursor.fetchone()

        if rev and rev['status'] == 'PENDING_APPROVAL':
            cursor.execute("SELECT txn_id, txn_type, customer_id, target_account, amount, status FROM transactions WHERE txn_id = ?", (rev['txn_id'],))
            txn = cursor.fetchone()

            if action == 'approve' and txn and txn['status'] != 'REVERSED':
                cust_id = txn['customer_id']
                amount = float(txn['amount'])
                txn_type = txn['txn_type']

                # REVERSE BALANCE CORRECTION
                if txn_type in ['DEPOSIT', 'MOBILE_TOPUP_REC']:
                    cursor.execute("UPDATE customers SET balance = balance - ? WHERE customer_id = ?", (amount, cust_id))
                elif txn_type in ['WITHDRAWAL', 'T24_TRANSFER', 'RTGS_TRANSFER', 'WALLET_TOPUP']:
                    cursor.execute("UPDATE customers SET balance = balance + ? WHERE customer_id = ?", (amount, cust_id))
                    if txn_type == 'T24_TRANSFER' and txn['target_account']:
                        cursor.execute("UPDATE customers SET balance = balance - ? WHERE customer_id = ?", (amount, txn['target_account']))

                cursor.execute("UPDATE transactions SET status = 'REVERSED' WHERE txn_id = ?", (txn['txn_id'],))
                cursor.execute("UPDATE reversals SET status = 'APPROVED' WHERE reversal_id = ?", (rev_id,))
                conn.commit()
                msg = f"✅ Transaction {txn['txn_id']} milkaa'inaan REVERSE ta'eera. Baalansiin deebi'eera!"

            elif action == 'reject':
                cursor.execute("UPDATE reversals SET status = 'REJECTED' WHERE reversal_id = ?", (rev_id,))
                conn.commit()
                msg = "❌ Gaaffiin Reversal Dhabatameera (Rejected)."

    cursor.execute("""
        SELECT r.reversal_id, r.txn_id, r.reason, r.requested_by, r.status, r.timestamp, t.amount, t.txn_type, t.customer_id
        FROM reversals r
        JOIN transactions t ON r.txn_id = t.txn_id
        ORDER BY r.timestamp DESC
    """)
    reversals = cursor.fetchall()
    conn.close()

    rows_html = ""
    for r in reversals:
        act_btn = ""
        if r['status'] == 'PENDING_APPROVAL':
            act_btn = f"""
            <form method="POST" style="display:inline;">
                <input type="hidden" name="reversal_id" value="{r['reversal_id']}">
                <button type="submit" name="action" value="approve" class="btn-action btn-green">Approve Reversal</button>
                <button type="submit" name="action" value="reject" class="btn-action btn-red">Reject</button>
            </form>
            """
        rows_html += f"""
        <tr style="border-bottom:1px solid #e2e8f0; font-size:12px;">
            <td style="padding:8px;">{r['timestamp']}</td>
            <td style="padding:8px;"><b>{r['txn_id']}</b> ({r['txn_type']})</td>
            <td style="padding:8px;">{r['customer_id']}</td>
            <td style="padding:8px;">{r['amount']:,.2f} Birr</td>
            <td style="padding:8px;">{r['reason']}</td>
            <td style="padding:8px;"><span class="badge badge-pending">{r['status']}</span></td>
            <td style="padding:8px;">{act_btn}</td>
        </tr>
        """

    content = f"""
    <div class="box">
        <h2>🔄 Gaaffii Reversal Transaction Mirkaneessuu</h2>
        {f"<p style='color:green; font-weight:bold;'>{msg}</p>" if msg else ""}
        <table style="width:100%; border-collapse:collapse; text-align:left;">
            <thead>
                <tr style="background:#f8fafc; font-size:11px;">
                    <th style="padding:8px;">Guyyaa</th>
                    <th style="padding:8px;">Txn ID</th>
                    <th style="padding:8px;">Acc No</th>
                    <th style="padding:8px;">Amount</th>
                    <th style="padding:8px;">Sababa</th>
                    <th style="padding:8px;">Status</th>
                    <th style="padding:8px;">Tarkaanfii</th>
                </tr>
            </thead>
            <tbody>
                {rows_html if rows_html else '<tr><td colspan="7" style="padding:16px; text-align:center;">Gaaffiin reversal hin jiru.</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content), notifications=NOTIFICATIONS)

@app.route('/')
def dashboard():
    if 'role' not in session:
        return redirect('/login')
    
    net_cap, deposits, withdraws, cust_bal, total_comm, mud_dep, mud_gross, mud_ceo, mud_cust = get_bank_capital()
    role = session['role']

    content = f"""
    <div class="card-net">
        <div class="net-title">Waliigala Kaabitaala Baankii (Net Capital)</div>
        <div class="net-amount">{net_cap:,.2f} Birr</div>
        <div class="net-grid">
            <div>📥 Deposit: <b>{deposits:,.2f} Birr</b></div>
            <div>📤 Withdraw/FT/RTGS: <b>{withdraws:,.2f} Birr</b></div>
        </div>
    </div>

    <h3 style="font-size: 14px; margin-bottom: 12px;">Menu Hojii ({role})</h3>
    <div class="grid-2">
        <a href="/transaction" class="btn-card"><span class="icon">💸</span><span>Deposit / Transfer / Withdraw</span></a>
        <a href="/reversals_list" class="btn-card"><span class="icon">🔄</span><span>Reversals</span></a>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content), notifications=NOTIFICATIONS)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
