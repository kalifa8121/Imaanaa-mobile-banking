import os
import sqlite3
import datetime
import random
import shutil
import sys
import time
import atexit
from io import BytesIO

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from flask import Flask, request, redirect, url_for, session, render_template_string, send_from_directory, jsonify, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "imana_free_interest_microfinance_secret_key"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
BACKUP_FOLDER = os.path.join(BASE_DIR, 'backups')
DB_PATH = os.path.join(BASE_DIR, "web_banking.db")

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['BACKUP_FOLDER'] = BACKUP_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)

NOTIFICATIONS = []

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

def get_db_connection(max_retries=10, delay=0.5):
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
            conn.execute("PRAGMA cache_size = -64000;")
            conn.execute("PRAGMA mmap_size = 268435456;")
            return conn
        except sqlite3.OperationalError as e:
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

def send_sms_alert(phone_number, message):
    print(f"📱 [SMS SENT TO {phone_number}]: {message}")

def add_notification(message):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    NOTIFICATIONS.insert(0, f"[{now}] {message}")
    if len(NOTIFICATIONS) > 20:
        NOTIFICATIONS.pop()

def perform_auto_backup():
    try:
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file_path = os.path.join(BACKUP_FOLDER, f"auto_backup_{now_str}.db")
        latest_path = os.path.join(BACKUP_FOLDER, "latest_auto_backup.db")
        
        if os.path.exists(DB_PATH):
            with sqlite3.connect(DB_PATH) as src_conn:
                with sqlite3.connect(backup_file_path) as dst_conn:
                    src_conn.backup(dst_conn)
                with sqlite3.connect(latest_path) as dst_conn2:
                    src_conn.backup(dst_conn2)
            print("💾 Auto Backup completed.")
    except Exception as e:
        print(f"❌ Auto Backup failed: {e}")

def perform_auto_restore():
    latest_path = os.path.join(BACKUP_FOLDER, "latest_auto_backup.db")
    if not os.path.exists(DB_PATH) and os.path.exists(latest_path):
        try:
            shutil.copyfile(latest_path, DB_PATH)
            print("🔄 Persistent Auto Restore completed.")
        except Exception as e:
            print(f"❌ Auto Restore failed: {e}")

perform_auto_restore()
atexit.register(perform_auto_backup)

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
            pin TEXT DEFAULT '1234',
            created_at TEXT
        )
    """)

    # Check and add PIN column if database existed previously
    try:
        cursor.execute("ALTER TABLE customers ADD COLUMN pin TEXT DEFAULT '1234'")
    except sqlite3.OperationalError:
        pass

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
            audited_status TEXT DEFAULT 'OPEN'
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

init_db()

def get_bank_capital():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE status='APPROVED' AND txn_type='DEPOSIT'")
    total_deposit = cursor.fetchone()[0] or 0.0
    
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE status='APPROVED' AND txn_type IN ('WITHDRAWAL', 'T24_TRANSFER')")
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

# --- STAFF WEB LAYOUT ---
HTML_LAYOUT = """
<!DOCTYPE html>
<html lang="om">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Imana Free Interest Microfinance - Staff</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background-color: #f8fafc; padding-bottom: 75px; color: #0f172a; }
        nav { background: linear-gradient(135deg, #065f46, #047857); color: white; padding: 12px 16px; position: sticky; top: 0; z-index: 50; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
        .logo-container { display: flex; align-items: center; gap: 10px; }
        .logo-svg { width: 32px; height: 32px; fill: #fbbf24; }
        nav h1 { font-size: 15px; font-weight: 800; letter-spacing: 0.3px; color: #ffffff; }
        .role-badge { background: #0284c7; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 11px; }
        .container { max-width: 600px; margin: 0 auto; padding: 16px; }
        .notification-bar { background: #fef3c7; color: #92400e; padding: 8px 12px; border-radius: 8px; font-size: 11px; margin-bottom: 12px; font-weight: bold; border: 1px solid #fde68a; }
        .card-net { background: linear-gradient(135deg, #064e3b, #047857); color: white; border-radius: 16px; padding: 20px; box-shadow: 0 10px 15px -3px rgba(6,78,59,0.3); margin-bottom: 20px; }
        .card-ceo-profit { background: linear-gradient(135deg, #4c1d95, #6b21a8); color: white; border-radius: 16px; padding: 20px; box-shadow: 0 10px 15px -3px rgba(76,29,149,0.3); margin-bottom: 20px; }
        .net-title { font-size: 12px; opacity: 0.9; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
        .net-amount { font-size: 32px; font-weight: 800; color: #fbbf24; }
        .net-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 16px; border-top: 1px solid rgba(255,255,255,0.2); font-size: 12px; padding-top: 12px; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .btn-card { background: white; padding: 16px; border-radius: 12px; border: 1px solid #e2e8f0; display: flex; flex-direction: column; align-items: center; text-decoration: none; color: #334155; font-weight: bold; font-size: 13px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.05); transition: 0.2s; }
        .btn-card:active { transform: scale(0.98); }
        .btn-card span.icon { font-size: 24px; margin-bottom: 8px; }
        .btn-card-ceo { background: #faf5ff; border-color: #e9d5ff; color: #581c87; }
        .btn-card-auditor { background: #fff7ed; border-color: #ffedd5; color: #c2410c; }
        .btn-card-loan { background: #f0fdf4; border-color: #bbf7d0; color: #15803d; }
        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: white; border-top: 1px solid #e2e8f0; display: flex; justify-content: space-around; padding: 10px 0; z-index: 50; }
        .bottom-nav a { text-align: center; color: #64748b; text-decoration: none; font-size: 11px; flex: 1; font-weight: 500; }
        .bottom-nav a span.icon { display: block; font-size: 18px; margin-bottom: 2px; }
        .box { background: white; padding: 20px; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-bottom: 16px; }
        .form-group { margin-bottom: 12px; position: relative; }
        .form-group label { display: block; font-size: 12px; font-weight: bold; color: #475569; margin-bottom: 4px; }
        .input-field { width: 100%; padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 14px; outline: none; }
        .btn-submit { width: 100%; background: #047857; color: white; border: none; padding: 12px; border-radius: 8px; font-weight: bold; font-size: 14px; cursor: pointer; }
        .badge { padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; display: inline-block; }
        .badge-pending { background: #fef3c7; color: #92400e; }
        .badge-active { background: #dcfce7; color: #166534; }
        .badge-danger { background: #fee2e2; color: #991b1b; }
        .badge-frozen { background: #dbeafe; color: #1e40af; border: 1px solid #93c5fd; }
        .badge-mudaraba { background: #f3e8ff; color: #6b21a8; border: 1px solid #d8b4fe; }
        .badge-wadia { background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd; }
        .item-card { background: white; border-radius: 12px; padding: 14px; margin-bottom: 12px; border: 1px solid #e2e8f0; }
        .img-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin: 10px 0; }
        .img-grid img { width: 100%; height: 60px; object-fit: cover; border-radius: 6px; border: 1px solid #e2e8f0; }
        .btn-action { padding: 6px 12px; border-radius: 6px; color: white; text-decoration: none; font-size: 12px; font-weight: bold; display: inline-block; border:none; cursor:pointer; }
        .btn-blue { background: #2563eb; }
        .btn-green { background: #16a34a; }
        .btn-red { background: #dc2626; }
        .btn-orange { background: #ea580c; }
        .btn-purple { background: #7c3aed; }
        .pwd-toggle { position: absolute; right: 10px; top: 32px; cursor: pointer; user-select: none; font-size: 14px; }
        @media print {
            .bottom-nav, nav, .btn-print, .no-print { display: none !important; }
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
            <a href="/maker_receipts"><span class="icon">🧾</span>Nagahee</a>
        {% endif %}
        {% if session['role'] == 'EXTERNAL_AGENT' %}
            <a href="/agent_register"><span class="icon">👤</span>Agent Maammila</a>
            <a href="/agent_transaction"><span class="icon">💸</span>Agent Txn</a>
        {% endif %}
        {% if session['role'] == 'MANAGER' %}
            <a href="/pending"><span class="icon">📋</span>Manager Appr</a>
            <a href="/reversals_list"><span class="icon">🔄</span>Reversals</a>
            <a href="/print_receipt_search"><span class="icon">🖨️</span>Nagahee</a>
        {% endif %}
        {% if session['role'] == 'AUDITOR' %}
            <a href="/pending"><span class="icon">📋</span>Auditor View</a>
            <a href="/auditor_reversal_request"><span class="icon">⚠️</span>Reversal Gaafachu</a>
            <a href="/print_receipt_search"><span class="icon">🖨️</span>Nagahee</a>
        {% endif %}
        {% if session['role'] in ['LOAN_OFFICER', 'CEO', 'MANAGER'] %}
            <a href="/islamic_loan"><span class="icon">📜</span>Liqaa Islaamaa</a>
        {% endif %}
        {% if session['role'] == 'CEO' %}
            <a href="/reversals_list" style="color: #581c87;"><span class="icon">🔄</span>Reversal CEO</a>
            <a href="/ceo_commission" style="color: #581c87;"><span class="icon">💰</span>Commission</a>
            <a href="/manage_users" style="color: #6b21a8;"><span class="icon">⚙️</span>Hojjattoota</a>
        {% endif %}
    </div>
    {% endif %}

    <script>
    function togglePasswordVisibility(inputId, toggleIconId) {
        var input = document.getElementById(inputId);
        var icon = document.getElementById(toggleIconId);
        if (input.type === "password") {
            input.type = "text";
            icon.textContent = "🙈";
        } else {
            input.type = "password";
            icon.textContent = "👁️";
        }
    }
    </script>
</body>
</html>
"""

# --- MOBILE APP LAYOUT FOR CUSTOMERS ---
MOBILE_LAYOUT = """
<!DOCTYPE html>
<html lang="om">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>IMANA Mobile Banking</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background-color: #f1f5f9; padding-bottom: 70px; color: #0f172a; }
        .mobile-header { background: linear-gradient(135deg, #065f46, #047857); color: white; padding: 18px 16px; border-bottom-left-radius: 20px; border-bottom-right-radius: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.15); }
        .header-top { display: flex; justify-content: space-between; align-items: center; }
        .app-title { font-size: 18px; font-weight: 800; color: #fbbf24; letter-spacing: 0.5px; }
        .user-greeting { font-size: 13px; margin-top: 10px; opacity: 0.95; }
        .app-container { padding: 16px; max-width: 480px; margin: 0 auto; }
        .balance-card { background: linear-gradient(135deg, #047857, #064e3b); color: white; border-radius: 18px; padding: 22px; box-shadow: 0 8px 20px rgba(6,78,59,0.3); margin-top: -15px; margin-bottom: 20px; text-align: center; }
        .balance-label { font-size: 12px; opacity: 0.85; text-transform: uppercase; letter-spacing: 0.5px; }
        .balance-val { font-size: 32px; font-weight: 900; color: #fbbf24; margin: 6px 0; }
        .acc-number { font-size: 12px; background: rgba(255,255,255,0.15); padding: 4px 12px; border-radius: 12px; display: inline-block; margin-top: 4px; }
        .quick-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }
        .action-btn { background: white; padding: 18px 12px; border-radius: 16px; border: 1px solid #e2e8f0; display: flex; flex-direction: column; align-items: center; text-decoration: none; color: #1e293b; font-weight: bold; font-size: 13px; box-shadow: 0 2px 5px rgba(0,0,0,0.04); text-align: center; }
        .action-btn span.icon { font-size: 26px; margin-bottom: 8px; }
        .bottom-m-nav { position: fixed; bottom: 0; left: 0; right: 0; background: white; border-top: 1px solid #e2e8f0; display: flex; justify-content: space-around; padding: 12px 0; z-index: 100; box-shadow: 0 -2px 10px rgba(0,0,0,0.05); }
        .bottom-m-nav a { text-align: center; color: #64748b; text-decoration: none; font-size: 11px; font-weight: 600; flex: 1; }
        .bottom-m-nav a span.icon { display: block; font-size: 20px; margin-bottom: 2px; }
        .m-box { background: white; padding: 20px; border-radius: 16px; border: 1px solid #e2e8f0; box-shadow: 0 2px 6px rgba(0,0,0,0.04); margin-bottom: 16px; }
        .m-input { width: 100%; padding: 12px; border: 1px solid #cbd5e1; border-radius: 10px; font-size: 15px; outline: none; margin-top: 4px; }
        .m-btn { width: 100%; background: #047857; color: white; border: none; padding: 14px; border-radius: 12px; font-weight: bold; font-size: 15px; cursor: pointer; margin-top: 10px; }
        .m-txn-item { display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #f1f5f9; font-size: 13px; }
    </style>
</head>
<body>
    <div class="mobile-header">
        <div class="header-top">
            <span class="app-title">📱 IMANA MOBILE</span>
            {% if session.get('is_customer') %}
                <a href="/mobile/logout" style="color:#fca5a5; font-size:12px; text-decoration:none; font-weight:bold;">Ba'i (Logout)</a>
            {% endif %}
        </div>
        {% if session.get('is_customer') %}
            <div class="user-greeting">Akkam, <b>{{ session['customer_name'] }}</b> 👋</div>
        {% endif %}
    </div>

    <div class="app-container">
        {% block content %}{% endblock %}
    </div>

    {% if session.get('is_customer') %}
    <div class="bottom-m-nav">
        <a href="/mobile/dashboard"><span class="icon">🏠</span>Fuula Duraa</a>
        <a href="/mobile/transfer"><span class="icon">💸</span>Dabarsii</a>
        <a href="/mobile/statement"><span class="icon">📜</span>Seenaa (History)</a>
        <a href="/mobile/set_pin"><span class="icon">🔐</span>PIN Jijjiiri</a>
    </div>
    {% endif %}
</body>
</html>
"""

# ==========================================
# CUSTOMER MOBILE BANKING ROUTES (/mobile/...)
# ==========================================

@app.route('/mobile/login', methods=['GET', 'POST'])
def mobile_login():
    error = None
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        pin = request.form.get('pin', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT customer_id, full_name, phone, balance, status, freeze_status, pin 
            FROM customers 
            WHERE (customer_id = ? OR phone = ?) AND pin = ?
        """, (identifier, identifier, pin))
        cust = cursor.fetchone()
        conn.close()

        if cust:
            if cust['status'] != 'ACTIVE':
                error = " Akkaawunttii keessan Mirkanaa'a jira (Pending Approval). Baankii qunnamaa."
            elif cust['freeze_status'] == 'FROZEN':
                error = "🔒 Akkaawunttii keessan Uggurameera! Baankii qunnamaa."
            else:
                session['is_customer'] = True
                session['customer_id'] = cust['customer_id']
                session['customer_name'] = cust['full_name']
                session['customer_phone'] = cust['phone']
                return redirect('/mobile/dashboard')
        else:
            error = "Lakkoofsa Bilbilaa/ID ykn PIN dogoggoraa!"

    content = f"""
    <div class="m-box" style="margin-top: 20px; text-align: center;">
        <div style="font-size: 42px; margin-bottom: 8px;">🏦</div>
        <h2 style="color:#065f46; font-size:18px;">IMANA Mobile Banking</h2>
        <p style="font-size:12px; color:#64748b; margin-bottom:16px;">Gara Mobile Banking Imanaatti Baga Nagaan Dhuftan</p>

        {f'<p style="color:#dc2626; font-size:12px; font-weight:bold; margin-bottom:12px; background:#fee2e2; padding:8px; border-radius:8px;">{error}</p>' if error else ''}

        <form method="POST" style="text-align:left;">
            <div style="margin-bottom:12px;">
                <label style="font-size:12px; font-weight:bold; color:#475569;">Lakk. Bilbilaa ykn ID Maammilaa</label>
                <input type="text" name="identifier" placeholder="Fkn: 100099008800 ykn 0911..." class="m-input" required>
            </div>
            <div style="margin-bottom:16px;">
                <label style="font-size:12px; font-weight:bold; color:#475569;">PIN Lakkoofsa 4</label>
                <input type="password" maxlength="4" name="pin" placeholder="****" class="m-input" style="letter-spacing:4px; font-size:18px; text-align:center;" required>
            </div>
            <button type="submit" class="m-btn">Seeni (Login)</button>
        </form>
    </div>
    <p style="text-align:center; font-size:11px; color:#64748b; margin-top:10px;">PIN keessan yoo dagattan baankii Imana qunnamaa.</p>
    """
    return render_template_string(MOBILE_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/mobile/dashboard')
def mobile_dashboard():
    if not session.get('is_customer'):
        return redirect('/mobile/login')

    cust_id = session['customer_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT customer_id, full_name, balance, account_type FROM customers WHERE customer_id = ?", (cust_id,))
    cust = cursor.fetchone()

    cursor.execute("""
        SELECT txn_type, amount, timestamp, ft_reference 
        FROM transactions 
        WHERE (customer_id = ? OR target_account = ?) AND status = 'APPROVED' 
        ORDER BY timestamp DESC LIMIT 5
    """, (cust_id, cust_id))
    recent_txns = cursor.fetchall()
    conn.close()

    txns_html = ""
    for t in recent_txns:
        is_credit = t['txn_type'] == 'DEPOSIT' or (t['txn_type'] == 'T24_TRANSFER' and t['target_account'] == cust_id)
        color = "#16a34a" if is_credit else "#dc2626"
        sign = "+" if is_credit else "-"
        txns_html += f"""
        <div class="m-txn-item">
            <div>
                <b>{t['txn_type']}</b><br>
                <span style="font-size:10px; color:#64748b;">{t['timestamp']}</span>
            </div>
            <div style="font-weight:bold; color:{color}; text-align:right;">
                {sign}{t['amount']:,.2f} Birr<br>
                <span style="font-size:9px; color:#94a3b8;">{t['ft_reference']}</span>
            </div>
        </div>
        """

    content = f"""
    <div class="balance-card">
        <div class="balance-label">Haftee Qarshii (Available Balance)</div>
        <div class="balance-val">{cust['balance']:,.2f} Birr</div>
        <div class="acc-number">Acc: {cust['customer_id']} ({cust['account_type']})</div>
    </div>

    <div class="quick-actions">
        <a href="/mobile/transfer" class="action-btn">
            <span class="icon">💸</span>
            <span>Qarshii Ergi</span>
        </a>
        <a href="/mobile/statement" class="action-btn">
            <span class="icon">📜</span>
            <span>Statement</span>
        </a>
    </div>

    <div class="m-box">
        <h3 style="font-size:14px; color:#065f46; margin-bottom:10px;">🕒 Socho'iinsa Dhiyootti (Recent Transactions)</h3>
        {txns_html if txns_html else '<p style="font-size:12px; color:#94a3b8; text-align:center;">Socho\'iinsi raawwatame hin jiru.</p>'}
    </div>
    """
    return render_template_string(MOBILE_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/mobile/transfer', methods=['GET', 'POST'])
def mobile_transfer():
    if not session.get('is_customer'):
        return redirect('/mobile/login')

    msg = None
    msg_type = "green"
    cust_id = session['customer_id']

    if request.method == 'POST':
        target_acc = request.form.get('target_account', '').strip()
        amount = float(request.form.get('amount', 0.0))
        pin = request.form.get('pin', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT balance, pin, freeze_status FROM customers WHERE customer_id = ?", (cust_id,))
        sender = cursor.fetchone()

        cursor.execute("SELECT full_name, status FROM customers WHERE customer_id = ?", (target_acc,))
        receiver = cursor.fetchone()

        if sender['pin'] != pin:
            msg = "❌ PIN Dogoggoraa!"
            msg_type = "red"
        elif sender['freeze_status'] == 'FROZEN':
            msg = "🔒 Akkaawunttii keessan Uggurameera!"
            msg_type = "red"
        elif not receiver:
            msg = "❌ Lakk. Akkaawuntii Nama Fudhatuu Hin Argamne!"
            msg_type = "red"
        elif target_acc == cust_id:
            msg = "❌ Akkaawuntii mataa keessaniitti ergachuu hin dandeessan!"
            msg_type = "red"
        elif amount <= 0:
            msg = "❌ Hamma maallaqaa sirrii galchaa!"
            msg_type = "red"
        elif sender['balance'] < amount:
            msg = f"❌ Balansii gahaa hin qabdan! (Jiru: {sender['balance']:,.2f} Birr)"
            msg_type = "red"
        else:
            timestamp_str = int(datetime.datetime.now().timestamp())
            ft_ref = f"MOB{datetime.datetime.now().strftime('%y%j')}{random.randint(10000, 99999)}"
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Deduct from Sender
            cursor.execute("UPDATE customers SET balance = balance - ? WHERE customer_id = ?", (amount, cust_id))
            # Add to Receiver
            cursor.execute("UPDATE customers SET balance = balance + ? WHERE customer_id = ?", (amount, target_acc))

            # Record Transaction directly as APPROVED
            cursor.execute("""
                INSERT INTO transactions (txn_id, txn_type, customer_id, customer_name, target_account, amount, commission, bank_name, ft_reference, status, created_by, timestamp)
                VALUES (?, 'T24_TRANSFER', ?, ?, ?, ?, 0.0, 'Imana Mobile App', ?, 'APPROVED', 'MOBILE_APP', ?)
            """, (f"TXN-MOB-{timestamp_str}", cust_id, session['customer_name'], target_acc, amount, ft_ref, now))

            conn.commit()
            msg = f"✅ Birr {amount:,.2f} gara {receiver['full_name']} ({target_acc}) milkaa'inaan ergameera! (Ref: {ft_ref})"
            add_notification(f"Mobile Transfer: {cust_id} -> {target_acc} ({amount} Birr)")

        conn.close()

    content = f"""
    <div class="m-box">
        <h2 style="font-size:16px; color:#065f46; margin-bottom:12px;">💸 Qarshii Dabarsi (Mobile Transfer)</h2>
        {f'<p style="background:{"#dcfce7" if msg_type=="green" else "#fee2e2"}; color:{"#166534" if msg_type=="green" else "#991b1b"}; padding:10px; border-radius:8px; font-size:12px; font-weight:bold; margin-bottom:12px;">{msg}</p>' if msg else ''}

        <form method="POST">
            <div style="margin-bottom:12px;">
                <label style="font-size:12px; font-weight:bold; color:#475569;">Lakk. Akkaawuntii Nama Fudhatuu</label>
                <input type="text" name="target_account" placeholder="Fkn: 100099008801" class="m-input" required>
            </div>
            <div style="margin-bottom:12px;">
                <label style="font-size:12px; font-weight:bold; color:#475569;">Hamma Qarshii (Amount Birr)</label>
                <input type="number" step="0.01" min="1" name="amount" placeholder="0.00" class="m-input" required>
            </div>
            <div style="margin-bottom:16px;">
                <label style="font-size:12px; font-weight:bold; color:#475569;">PIN Keessan Galchaa</label>
                <input type="password" maxlength="4" name="pin" placeholder="****" class="m-input" style="text-align:center; letter-spacing:4px;" required>
            </div>
            <button type="submit" class="m-btn">🚀 Dabarsi (Send Money)</button>
        </form>
    </div>
    """
    return render_template_string(MOBILE_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/mobile/statement')
def mobile_statement():
    if not session.get('is_customer'):
        return redirect('/mobile/login')

    cust_id = session['customer_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, balance FROM customers WHERE customer_id = ?", (cust_id,))
    c = cursor.fetchone()

    cursor.execute("""
        SELECT txn_type, amount, timestamp, ft_reference, target_account, customer_id
        FROM transactions 
        WHERE (customer_id = ? OR target_account = ?) AND status = 'APPROVED'
        ORDER BY timestamp DESC
    """, (cust_id, cust_id))
    txns = cursor.fetchall()
    conn.close()

    txns_html = ""
    for t in txns:
        is_credit = t['txn_type'] == 'DEPOSIT' or (t['txn_type'] == 'T24_TRANSFER' and t['target_account'] == cust_id)
        color = "#16a34a" if is_credit else "#dc2626"
        sign = "+" if is_credit else "-"
        txns_html += f"""
        <div class="m-txn-item">
            <div>
                <b>{t['txn_type']}</b><br>
                <span style="font-size:10px; color:#64748b;">Ref: {t['ft_reference']} | {t['timestamp']}</span>
            </div>
            <div style="font-weight:bold; color:{color}; text-align:right;">
                {sign}{t['amount']:,.2f} Birr
            </div>
        </div>
        """

    content = f"""
    <div class="m-box">
        <h2 style="font-size:16px; color:#065f46; margin-bottom:4px;">📜 Seenaa Herregaa (Mini Statement)</h2>
        <p style="font-size:12px; color:#64748b; margin-bottom:12px;">Haftee Amajji: <b>{c['balance']:,.2f} Birr</b></p>
        <div style="border-top:1px dashed #cbd5e1; padding-top:8px;">
            {txns_html if txns_html else '<p style="font-size:12px; color:#94a3b8; text-align:center; padding:20px;">Socho\'iinsi raawwatame hin jiru.</p>'}
        </div>
    </div>
    """
    return render_template_string(MOBILE_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/mobile/set_pin', methods=['GET', 'POST'])
def mobile_set_pin():
    if not session.get('is_customer'):
        return redirect('/mobile/login')

    msg = None
    msg_type = "green"
    cust_id = session['customer_id']

    if request.method == 'POST':
        old_pin = request.form.get('old_pin', '').strip()
        new_pin = request.form.get('new_pin', '').strip()
        confirm_pin = request.form.get('confirm_pin', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT pin FROM customers WHERE customer_id = ?", (cust_id,))
        c = cursor.fetchone()

        if c['pin'] != old_pin:
            msg = "❌ PIN Duraanii Dogoggoraa!"
            msg_type = "red"
        elif new_pin != confirm_pin:
            msg = "❌ PIN Haaraa fi Mirkaneessaan Wal Hin Simne!"
            msg_type = "red"
        elif len(new_pin) != 4 or not new_pin.isdigit():
            msg = "❌ PIN Lakkoofsa 4 Qofa Ta'uu Qaba!"
            msg_type = "red"
        else:
            cursor.execute("UPDATE customers SET pin = ? WHERE customer_id = ?", (new_pin, cust_id))
            conn.commit()
            msg = "✅ PIN keessan milkaa'inaan jijjiiramtaniirra!"

        conn.close()

    content = f"""
    <div class="m-box">
        <h2 style="font-size:16px; color:#065f46; margin-bottom:12px;">🔐 PIN Jijjiiri (Change PIN)</h2>
        {f'<p style="background:{"#dcfce7" if msg_type=="green" else "#fee2e2"}; color:{"#166534" if msg_type=="green" else "#991b1b"}; padding:10px; border-radius:8px; font-size:12px; font-weight:bold; margin-bottom:12px;">{msg}</p>' if msg else ''}

        <form method="POST">
            <div style="margin-bottom:12px;">
                <label style="font-size:12px; font-weight:bold; color:#475569;">PIN Duraanii</label>
                <input type="password" maxlength="4" name="old_pin" class="m-input" style="text-align:center; letter-spacing:4px;" required>
            </div>
            <div style="margin-bottom:12px;">
                <label style="font-size:12px; font-weight:bold; color:#475569;">PIN Haaraa (Lakkoofsa 4)</label>
                <input type="password" maxlength="4" name="new_pin" class="m-input" style="text-align:center; letter-spacing:4px;" required>
            </div>
            <div style="margin-bottom:16px;">
                <label style="font-size:12px; font-weight:bold; color:#475569;">PIN Haaraa Mirkaneessi</label>
                <input type="password" maxlength="4" name="confirm_pin" class="m-input" style="text-align:center; letter-spacing:4px;" required>
            </div>
            <button type="submit" class="m-btn">💾 PIN Jijjiiri</button>
        </form>
    </div>
    """
    return render_template_string(MOBILE_LAYOUT.replace("{% block content %}{% endblock %}", content))

@app.route('/mobile/logout')
def mobile_logout():
    session.pop('is_customer', None)
    session.pop('customer_id', None)
    session.pop('customer_name', None)
    session.pop('customer_phone', None)
    return redirect('/mobile/login')

# ==========================================
# STAFF SYSTEM ROUTES (EXISTING CODE)
# ==========================================

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/get_customer/<cust_id>')
def api_get_customer(cust_id):
    if 'role' not in session and not session.get('is_customer'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT customer_id, full_name, phone, photo_path, signature_path, freeze_status, freeze_reason, balance FROM customers WHERE customer_id = ?", (cust_id,))
    cust = cursor.fetchone()
    conn.close()
    
    if cust:
        return jsonify({
            'success': True,
            'customer_id': cust['customer_id'],
            'full_name': cust['full_name'],
            'phone': cust['phone'],
            'photo_path': cust['photo_path'],
            'signature_path': cust['signature_path'],
            'freeze_status': cust['freeze_status'],
            'freeze_reason': cust['freeze_reason'],
            'balance': cust['balance']
        })
    return jsonify({'success': False, 'message': 'Maammilli hin argamne'})

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
                error = "🚫 Akkaawunttii keessan UGGURAMEERA! CEO qunnamaa."
            else:
                session['username'] = user['username']
                session['role'] = user['role']
                return redirect('/')
        else:
            error = "Username ykn Password dogoggoraa!"

    err_html = f"<p style='color:red; font-size:12px; text-align:center; margin-bottom:12px;'>{error}</p>" if error else ""
    content = f"""
    <div class="box" style="margin-top: 30px; text-align: center;">
        <div style="font-size: 40px; margin-bottom: 10px;">🏦</div>
        <h2 style="font-size: 17px; margin-bottom: 4px; color:#065f46;">Imana Free Interest Microfinance</h2>
        <p style="font-size: 12px; color: #64748b; margin-bottom: 16px;">Seensa Systema Staff (Login)</p>
        {err_html}
        <form method="POST">
            <div class="form-group" style="text-align:left;">
                <label>Username</label>
                <input type="text" name="username" placeholder="Fkn: ceo, manager1, maker1" class="input-field" required>
            </div>
            <div class="form-group" style="text-align:left;">
                <label>Password</label>
                <input type="password" id="login_password" name="password" placeholder="Password" class="input-field" required>
                <span id="login_pwd_toggle" class="pwd-toggle" onclick="togglePasswordVisibility('login_password', 'login_pwd_toggle')">👁️</span>
            </div>
            <button type="submit" class="btn-submit">Seeni (Staff Login)</button>
        </form>
        <p style="margin-top:15px; font-size:12px;"><a href="/mobile/login" style="color:#047857; font-weight:bold; text-decoration:none;">📱 Seensa Mobile Banking Maammiltootaa (Customer Portal)</a></p>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content), notifications=NOTIFICATIONS)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'role' not in session:
        return redirect('/login')

    msg = None
    msg_type = "green"

    if request.method == 'POST':
        old_pwd = request.form.get('old_password', '').strip()
        new_pwd = request.form.get('new_password', '').strip()
        confirm_pwd = request.form.get('confirm_password', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM users WHERE username = ?", (session['username'],))
        user = cursor.fetchone()

        if not user or user['password'] != old_pwd:
            msg = "❌ Password duraanii dogoggoraa!"
            msg_type = "red"
        elif new_pwd != confirm_pwd:
            msg = "❌ Password-ni haaraa fi Mirkaneessaan wal hin simne!"
            msg_type = "red"
        elif len(new_pwd) < 4:
            msg = "❌ Password-ni haaraa xiqqaate gabaabaa dha (Minimum 4 characters)!"
            msg_type = "red"
        else:
            cursor.execute("UPDATE users SET password = ? WHERE username = ?", (new_pwd, session['username']))
            conn.commit()
            msg = "✅ Password keessan milkaa'inaan jijjiiramtaniirra!"
            msg_type = "green"

        conn.close()

    content = f"""
    <div class="box">
        <h2 style="font-size: 16px; color:#065f46; margin-bottom: 12px;">🔑 Password Mataa Keetii Jijjiiri</h2>
        {f"<p style='background:{'#dcfce7' if msg_type=='green' else '#fee2e2'}; color:{'#166534' if msg_type=='green' else '#991b1b'}; padding:10px; border-radius:6px; font-size:12px; font-weight:bold; margin-bottom:12px;'>{msg}</p>" if msg else ""}
        <form method="POST">
            <div class="form-group">
                <label>Password Duraanii (Current Password)</label>
                <input type="password" id="old_pwd" name="old_password" required class="input-field">
                <span id="old_pwd_toggle" class="pwd-toggle" onclick="togglePasswordVisibility('old_pwd', 'old_pwd_toggle')">👁️</span>
            </div>
            <div class="form-group">
                <label>Password Haaraa (New Password)</label>
                <input type="password" id="new_pwd" name="new_password" required class="input-field">
                <span id="new_pwd_toggle" class="pwd-toggle" onclick="togglePasswordVisibility('new_pwd', 'new_pwd_toggle')">👁️</span>
            </div>
            <div class="form-group">
                <label>Password Haaraa Mirkaneessi (Confirm Password)</label>
                <input type="password" id="conf_pwd" name="confirm_password" required class="input-field">
                <span id="conf_pwd_toggle" class="pwd-toggle" onclick="togglePasswordVisibility('conf_pwd', 'conf_pwd_toggle')">👁️</span>
            </div>
            <button type="submit" class="btn-submit">💾 Password Jijjiiri</button>
        </form>
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content), notifications=NOTIFICATIONS)

@app.route('/')
def dashboard():
    if 'role' not in session:
        return redirect('/login')
    
    net_cap, deposits, withdraws, cust_bal, total_comm, mud_dep, mud_gross, mud_ceo, mud_cust = get_bank_capital()
    role = session['role']

    maker_btns = ""
    if role == 'MAKER':
        maker_btns = """
        <a href="/register" class="btn-card"><span class="icon">👤</span><span>Galmee Maammilaa</span></a>
        <a href="/transaction" class="btn-card"><span class="icon">💸</span><span>Deposit / Transfer / Withdraw</span></a>
        <a href="/maker_receipts" class="btn-card"><span class="icon">🧾</span><span>Nagahee Maxxansi</span></a>
        """

    agent_btns = ""
    if role == 'EXTERNAL_AGENT':
        agent_btns = """
        <a href="/agent_register" class="btn-card btn-card-ceo"><span class="icon">👤</span><span>External Agent Maammila Uumi</span></a>
        <a href="/agent_transaction" class="btn-card btn-card-ceo"><span class="icon">💸</span><span>External Agent Txn (10% Comm)</span></a>
        """

    manager_btns = ""
    if role == 'MANAGER':
        manager_btns = """
        <a href="/pending" class="btn-card"><span class="icon">🔍</span><span>Manager Approval & Reversal View</span></a>
        <a href="/reversals_list" class="btn-card"><span class="icon">🔄</span><span>Reversal Approvals</span></a>
        <a href="/print_receipt_search" class="btn-card"><span class="icon">🖨️</span><span>Barbaadi & Nagahee Maxxansi</span></a>
        """

    auditor_btns = ""
    if role == 'AUDITOR':
        auditor_btns = """
        <a href="/pending" class="btn-card btn-card-auditor"><span class="icon">📋</span><span>View Maammilaa & Approve</span></a>
        <a href="/auditor_reversal_request" class="btn-card btn-card-auditor"><span class="icon">⚠️</span><span>Transaction Reversal Gaafachu</span></a>
        <a href="/auditor_eod" class="btn-card btn-card-auditor"><span class="icon">📊</span><span>EOD Cufiinsa & Gabaasa Maker</span></a>
        <a href="/print_receipt_search" class="btn-card btn-card-auditor"><span class="icon">🖨️</span><span>Txn Nagahee Maxxansi</span></a>
        """

    loan_btn = ""
    if role in ['LOAN_OFFICER', 'CEO', 'MANAGER']:
        loan_btn = """
        <a href="/islamic_loan" class="btn-card btn-card-loan"><span class="icon">📜</span><span>Mudaraba & Murabaha Loan</span></a>
        """

    ceo_btn = ""
    ceo_mudaraba_dashboard = ""
    net_capital_html = ""
    
    if role == 'CEO':
        ceo_mudaraba_dashboard = f"""
        <div class="card-ceo-profit">
            <div class="net-title">📊 CEO Private View: Mudaraba 50/50 Profit Share</div>
            <div class="net-amount">{mud_ceo:,.2f} Birr</div>
            <p style="font-size:11px; opacity:0.9; margin-top:4px;">Qoodda Bu'aa Baankii/CEO (50% Share)</p>
            <div class="net-grid">
                <div>📈 Waliigala Kuusaa Mudaraba: <b>{mud_dep:,.2f} Birr</b></div>
                <div>🤝 Qoodda Maammiltootaa (50%): <b>{mud_cust:,.2f} Birr</b></div>
            </div>
        </div>
        """
        net_capital_html = f"""
        <div class="card-net">
            <div class="net-title">Waliigala Kaabitaala Baankii (Net Capital)</div>
            <div class="net-amount">{net_cap:,.2f} Birr</div>
            <div class="net-grid">
                <div>📥 Deposit: <b>{deposits:,.2f} Birr</b></div>
                <div>📤 Withdraw/FT: <b>{withdraws:,.2f} Birr</b></div>
            </div>
        </div>
        """
        ceo_btn = """
        <a href="/ceo_commission" class="btn-card btn-card-ceo"><span class="icon">💰</span><span>Comishina Guyyaa (Filtara)</span></a>
        <a href="/ceo_mudaraba_list" class="btn-card btn-card-ceo"><span class="icon">🤝</span><span>Mudaraba Private List</span></a>
        <a href="/reversals_list" class="btn-card btn-card-ceo"><span class="icon">🔄</span><span>CEO Reversal Approval</span></a>
        <a href="/manage_users" class="btn-card btn-card-ceo"><span class="icon">⚙️</span><span>Bulchiinsa Hojjattootaa & Role</span></a>
        """

    content = f"""
    {ceo_mudaraba_dashboard}
    {net_capital_html}

    <h3 style="font-size: 14px; margin-bottom: 12px; color: #475569;">Menu Hojii ({role})</h3>
    <div class="grid-2">
        {maker_btns}
        {agent_btns}
        {manager_btns}
        {auditor_btns}
        {loan_btn}
        <a href="/customers" class="btn-card"><span class="icon">👥</span><span>Listii Maammiltootaa</span></a>
        {ceo_btn}
    </div>
    """
    return render_template_string(HTML_LAYOUT.replace("{% block content %}{% endblock %}", content), notifications=NOTIFICATIONS)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
