import json
import os
import random
import re
import secrets
import smtplib
import sqlite3
import string
import time
from datetime import datetime
from email.message import EmailMessage
from functools import wraps
from flask import Flask, request, redirect, url_for, session, Response, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from PIL import Image, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Përdorimi i Firebase Admin për njoftime native të sigurta në Android & iOS
try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    HAS_FIREBASE = True
except ImportError:
    HAS_FIREBASE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

SCRIPT_MTIME = os.path.getmtime(__file__) if os.path.exists(__file__) else 0
IG_USERNAME = "grande_studio3"
IG_WEB_URL = f"https://www.instagram.com/{IG_USERNAME}/"
IG_APP_URL = f"instagram://user?username={IG_USERNAME}"

MSG_ACCEPTED = "Termini juaj u pranua dhe u rezervua, jeni të mirëseardhur."
MSG_REJECTED = "Na kërkoni ndjesë, nuk mund ta pranojmë terminin tuaj, provoni një ditë tjetër."

app = Flask(__name__, static_folder=STATIC_DIR)
app.secret_key = os.environ.get("SECRET_KEY") or "grande_studio_persistent_secret_key_2026"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 315360000

ADMIN_REMEMBER_COOKIE = "grande_admin_remember"
CLIENT_REMEMBER_COOKIE = "grande_client_remember"
COOKIE_MAX_AGE = 315360000

DB_PATH = os.path.join(DATA_DIR, "grande_studio.db")
FIREBASE_KEY_PATH = os.path.join(BASE_DIR, "serviceAccountKey.json")

WORK_START = "09:00"
WORK_END = "18:30"
SLOT_STEP_MIN = 30

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or "noreply@grandestudio.local")

# Inicializimi i Firebase Admin SDK
if HAS_FIREBASE and os.path.exists(FIREBASE_KEY_PATH):
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"[FIREBASE] Gabim gjatë inicializimit: {e}")


def get_asset_version() -> str:
    mtimes = [SCRIPT_MTIME]
    for name in ("ig_avatar_clean.png", "icon-192.png", "icon-512.png"):
        p = os.path.join(STATIC_DIR, name)
        if os.path.exists(p):
            mtimes.append(os.path.getmtime(p))
    return str(int(max(mtimes)))


def normalize_ig_handle(raw: str) -> str:
    cleaned = (raw or "").strip()
    cleaned = re.sub(r"^https?://(www\.)?instagram\.com/", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip("/").lstrip("@").strip()
    return f"@{cleaned}" if cleaned else ""


def prepare_clean_nails_asset() -> str | None:
    for preferred in ("thonjet_clean_3.png", "thonjet_clean_2.png", "thonjet_clean.png"):
        p = os.path.join(STATIC_DIR, preferred)
        if os.path.exists(p):
            return f"/static/{preferred}"
    return None


def prepare_ig_avatar_asset() -> str | None:
    for preferred in ("ig_avatar_clean_4.png", "ig_avatar_clean.png"):
        p = os.path.join(STATIC_DIR, preferred)
        if os.path.exists(p):
            return f"/static/{preferred}"
    return None


def prepare_app_icons() -> None:
    pass


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_sessions (
            token TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_sessions (
            token TEXT PRIMARY KEY,
            client_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (client_id) REFERENCES client_accounts (id) ON DELETE CASCADE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auth_method TEXT NOT NULL DEFAULT 'instagram',
            instagram TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_blocked INTEGER DEFAULT 0,
            password_reset_required INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            duration_min INTEGER NOT NULL,
            price REAL NOT NULL,
            description TEXT DEFAULT ''
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            client_name TEXT NOT NULL,
            client_phone TEXT NOT NULL,
            client_instagram TEXT DEFAULT '',
            client_email TEXT DEFAULT '',
            service_id INTEGER,
            appt_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'Konfirmuar',
            is_new INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (client_id) REFERENCES client_accounts (id) ON DELETE SET NULL,
            FOREIGN KEY (service_id) REFERENCES services (id) ON DELETE SET NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fcm_token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            fcm_token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (client_id) REFERENCES client_accounts (id) ON DELETE CASCADE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            event_type TEXT DEFAULT 'info',
            is_read INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (client_id) REFERENCES client_accounts (id) ON DELETE CASCADE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _send_fcm_notification(tokens, title: str, body: str, url: str = "/") -> None:
    if not HAS_FIREBASE or not tokens:
        return
    dead_tokens = []
    for tok in tokens:
        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data={"url": url},
                token=tok,
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(sound="default", click_action="FCM_PLUGIN_ACTIVITY")
                )
            )
            messaging.send(message)
        except Exception as ex:
            if "NOT_FOUND" in str(ex) or "UNREGISTERED" in str(ex):
                dead_tokens.append(tok)
    if dead_tokens:
        conn = get_db()
        cur = conn.cursor()
        for dt in dead_tokens:
            cur.execute("DELETE FROM push_subscriptions WHERE fcm_token = ?", (dt,))
            cur.execute("DELETE FROM client_push_subscriptions WHERE fcm_token = ?", (dt,))
        conn.commit()
        conn.close()


def send_push_to_admins(title: str, body: str, url: str = "/admin?play_sound=1") -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO admin_notifications (title, body, is_read, created_at) VALUES (?, ?, 0, ?)",
        (title, body, datetime.now().isoformat())
    )
    cur.execute("SELECT fcm_token FROM push_subscriptions")
    subs = [r["fcm_token"] for r in cur.fetchall()]
    conn.commit()
    conn.close()
    _send_fcm_notification(subs, title, body, url=url)


def notify_client_message(client_id: int | None, title: str, body: str, event_type: str = "info") -> None:
    if not client_id:
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO client_notifications (client_id, title, body, event_type, is_read, created_at) VALUES (?, ?, ?, ?, 0, ?)",
        (client_id, title, body, event_type, datetime.now().isoformat()),
    )
    cur.execute("SELECT fcm_token FROM client_push_subscriptions WHERE client_id = ?", (client_id,))
    subs = [r["fcm_token"] for r in cur.fetchall()]
    conn.commit()
    conn.close()
    _send_fcm_notification(subs, title, body, url="/")


def notify_client_appointment_decision(client_id: int | None, accepted: bool) -> None:
    if not client_id:
        return
    title = "Grande Studio — Termini u Pranua" if accepted else "Grande Studio — Njoftim për Terminin"
    body = MSG_ACCEPTED if accepted else MSG_REJECTED
    event_type = "accepted" if accepted else "rejected"
    notify_client_message(client_id, title, body, event_type=event_type)


def get_admin_account():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_account WHERE id = 1")
    acc = cur.fetchone()
    conn.close()
    return acc


def is_admin_authenticated() -> bool:
    if session.get("admin_logged_in"):
        return True
    cookie_tok = request.cookies.get(ADMIN_REMEMBER_COOKIE, "")
    if cookie_tok:
        session["admin_logged_in"] = True
        return True
    return False


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin_authenticated():
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


def time_to_min(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def min_to_time(minutes: int) -> str:
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def get_available_slots(appt_date: str, duration_min: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT start_time, end_time FROM appointments WHERE appt_date = ? AND status IN ('Konfirmuar', 'Bllokuar')",
        (appt_date,),
    )
    busy = [(time_to_min(r["start_time"]), time_to_min(r["end_time"])) for r in cur.fetchall()]
    conn.close()

    start_m = time_to_min(WORK_START)
    end_m = time_to_min(WORK_END)

    slots = []
    curr = start_m
    while curr + duration_min <= end_m:
        cand_start = curr
        cand_end = curr + duration_min
        overlap = any(not (cand_end <= b_start or cand_start >= b_end) for b_start, b_end in busy)
        if not overlap:
            slots.append(min_to_time(cand_start))
        curr += SLOT_STEP_MIN
    return slots


def get_banner_artwork_html() -> str:
    nails_url = prepare_clean_nails_asset()
    ig_avatar_url = prepare_ig_avatar_asset()
    v = get_asset_version()
    ig_top_html = f'<img src="{ig_avatar_url}?v={v}" alt="Instagram" class="ig-banner-avatar">' if ig_avatar_url else ''
    nails_bottom_html = f'<img src="{nails_url}?v={v}" alt="Nails" class="nails-banner-bottom">' if nails_url else ''
    return f"{ig_top_html}\n{nails_bottom_html}"


def get_ig_nav_button_html() -> str:
    return f"""
    <a href="{IG_WEB_URL}" data-ig-app="{IG_APP_URL}" data-ig-web="{IG_WEB_URL}" onclick="openInstagramProfile(event)" class="btn-3d btn-ig-3d">
      <span>IG</span>
    </a>
    """


BASE_STYLE = """
<style>
  :root { --vishnje: #72091d; --vishnje-bright: #b81d3d; --vishnje-dark: #45030f; }
  body { margin:0; font-family: sans-serif; background: #26030a; color: #261217; display:flex; justify-content:center; padding:12px; }
  .iphone-chassis { width: 412px; max-width: 100%; background: #141112; border-radius: 40px; padding: 10px; }
  .iphone-screen { background: #fdf5f7; border-radius: 32px; overflow: hidden; min-height: 800px; display:flex; flex-direction:column; }
  header { background: var(--vishnje); color: #fff; padding: 12px; display:flex; justify-content:space-between; align-items:center; }
  .container { padding: 12px; flex:1; overflow-y:auto; }
  .card-3d { background: #fff; border-radius: 14px; padding: 12px; margin-bottom: 12px; border: 1px solid #edd3da; }
  .btn-3d { border:none; border-radius: 10px; padding: 10px; font-weight:700; cursor:pointer; text-decoration:none; display:inline-block; text-align:center; }
  .btn-vishnje-3d { background: var(--vishnje); color:#fff; }
  .btn-ig-3d { background: #dd2a7b; color:#fff; padding:6px 10px; }
  input, select { width:100%; padding:10px; margin-top:4px; margin-bottom:10px; border:1px solid #e2c2ca; border-radius:8px; }
  table { width:100%; border-collapse:collapse; font-size:0.85rem; }
  th, td { padding:6px; border-bottom:1px solid #edd3da; text-align:left; }
  .badge { padding:2px 6px; border-radius:99px; font-size:0.75rem; font-weight:bold; }
  .b-ok { background:#d3f9d8; color:#1f7a3d; }
  .b-can { background:#ffe3e3; color:#b41e2d; }
  .btn-sm-3d { padding:4px 8px; font-size:0.75rem; border-radius:6px; border:none; cursor:pointer; }
  .btn-del { background:#ffe3e3; color:#b41e2d; }
  .btn-seen { background:#dcfce7; color:#166534; }
</style>
"""


def render_iphone_shell(title: str, inner_html: str) -> str:
    return f"""
    <!doctype html>
    <html lang="sq">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
      <title>{title}</title>
      {BASE_STYLE}
    </head>
    <body>
      <div class="iphone-chassis">
        <div class="iphone-screen">
          <div style="flex:1; overflow-y:auto;">{inner_html}</div>
        </div>
      </div>
      <script>
        function playNotificationChime() {{
          try {{
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.connect(gain); gain.connect(ctx.destination);
            osc.frequency.setValueAtTime(880, ctx.currentTime);
            gain.gain.setValueAtTime(0.3, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
            osc.start(); osc.stop(ctx.currentTime + 0.4);
          }} catch (e) {{}}
        }}

        // Lidhja me Capacitor Push Notifications për APK-në tënde
        document.addEventListener('DOMContentLoaded', () => {{
          if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PushNotifications) {{
            const {{ PushNotifications }} = window.Capacitor.Plugins;
            PushNotifications.requestPermissions().then(result => {{
              if (result.receive === 'granted') {{ PushNotifications.register(); }}
            }});

            PushNotifications.addListener('registration', (token) => {{
              const fcmToken = token.value;
              const isAdmin = window.location.pathname.startsWith('/admin');
              const endpointUrl = isAdmin ? '/api/push/subscribe-fcm' : '/api/client/push/subscribe-fcm';
              fetch(endpointUrl, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ fcm_token: fcmToken }})
              }}).catch(() => {{}});
            }});

            PushNotifications.addListener('pushNotificationReceived', (notification) => {{
              playNotificationChime();
            }});
          }}
        }));
      </script>
    </body>
    </html>
    """


@app.route("/api/push/subscribe-fcm", methods=["POST"])
def api_push_subscribe_fcm():
    data = request.get_json(silent=True) or {}
    token = data.get("fcm_token")
    if not token:
        return jsonify({"ok": False}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO push_subscriptions (fcm_token, created_at) VALUES (?, ?) ON CONFLICT(fcm_token) DO UPDATE SET created_at = excluded.created_at",
        (token, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/client/push/subscribe-fcm", methods=["POST"])
def api_client_push_subscribe_fcm():
    client_id = session.get("client_id")
    if not client_id:
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    token = data.get("fcm_token")
    if not token:
        return jsonify({"ok": False}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO client_push_subscriptions (client_id, fcm_token, created_at) VALUES (?, ?, ?) ON CONFLICT(fcm_token) DO UPDATE SET client_id = excluded.client_id",
        (client_id, token, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/slots")
def api_slots():
    appt_date = request.args.get("date", "")
    service_id = request.args.get("service_id", type=int)
    if not appt_date or not service_id:
        return jsonify({"slots": []})
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT duration_min FROM services WHERE id = ?", (service_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"slots": []})
    slots = get_available_slots(appt_date, row["duration_min"])
    return jsonify({"slots": slots})


@app.route("/", methods=["GET", "POST"])
def index():
    init_db()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM services ORDER BY id")
    services = cur.fetchall()

    msg = None
    msg_type = "ok"
    today_str = datetime.now().strftime("%Y-%m-%d")

    if request.method == "POST":
        form_type = request.form.get("form_type", "booking")
        if form_type == "client_register":
            identifier = request.form.get("reg_instagram", "").strip()
            phone = request.form.get("reg_phone", "").strip()
            pwd = request.form.get("reg_password", "")
            if identifier and phone and len(pwd) >= 6:
                cur.execute("SELECT id FROM client_accounts WHERE instagram = ? OR email = ?", (identifier, identifier))
                if cur.fetchone():
                    msg = "Llogaria ekziston."
                    msg_type = "err"
                else:
                    cur.execute(
                        "INSERT INTO client_accounts (instagram, phone, password_hash, created_at) VALUES (?, ?, ?, ?)",
                        (identifier, phone, generate_password_hash(pwd), datetime.now().isoformat())
                    )
                    conn.commit()
                    session["client_id"] = cur.lastrowid
                    msg = "U regjistruat me sukses!"
            else:
                msg = "Plotësoni të gjitha fushat saktë."
                msg_type = "err"
        elif form_type == "client_login":
            identifier = request.form.get("login_identifier", "").strip()
            pwd = request.form.get("login_password", "")
            cur.execute("SELECT * FROM client_accounts WHERE instagram = ? OR email = ?", (identifier, identifier))
            acc = cur.fetchone()
            if acc and check_password_hash(acc["password_hash"], pwd):
                session["client_id"] = acc["id"]
                msg = "U kyçët me sukses!"
            else:
                msg = "Të dhëna të pasakta."
                msg_type = "err"
        elif form_type == "client_logout":
            session.pop("client_id", None)
            msg = "Jeni çkyçur."
        else:
            client_id = session.get("client_id")
            if not client_id:
                msg = "Duhet të kyçeni për të rezervuar."
                msg_type = "err"
            else:
                service_id = request.form.get("service_id", type=int)
                appt_date = request.form.get("appt_date", "")
                start_time = request.form.get("start_time", "")
                cur.execute("SELECT * FROM services WHERE id = ?", (service_id,))
                svc = cur.fetchone()
                if svc and appt_date and start_time:
                    end_time = min_to_time(time_to_min(start_time) + svc["duration_min"])
                    cur.execute("SELECT instagram, email, phone FROM client_accounts WHERE id = ?", (client_id,))
                    cl = cur.fetchone()
                    c_name = cl["instagram"] or cl["email"]
                    cur.execute(
                        """
                        INSERT INTO appointments (client_id, client_name, client_phone, client_instagram, service_id, appt_date, start_time, end_time, status, is_new, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Konfirmuar', 1, ?)
                        """,
                        (client_id, c_name, cl["phone"], cl["instagram"], service_id, appt_date, start_time, end_time, datetime.now().isoformat())
                    )
                    conn.commit()
                    send_push_to_admins(title=f"🔔 Rezervim i Ri: {c_name}", body=f"{svc['name']} • {appt_date} {start_time}")
                    msg = "Rezervimi u dërgua me sukses!"
                else:
                    msg = "Zgjidhni shërbimin, datën dhe orarin."
                    msg_type = "err"

    logged_client = None
    if session.get("client_id"):
        cur.execute("SELECT * FROM client_accounts WHERE id = ?", (session["client_id"],))
        logged_client = cur.fetchone()
    conn.close()

    service_options = "\n".join(f'<option value="{s["id"]}">[{s["category"]}] {s["name"]} ({s["price"]:.0f}€)</option>' for s in services)
    alert_html = f'<div style="padding:10px; border-radius:8px; margin-bottom:10px; background:{"#ebfaef" if msg_type=="ok" else "#fff0f2"}; color:{"#1f7a3d" if msg_type=="ok" else "#b41e2d"};">{msg}</div>' if msg else ""

    auth_section = ""
    if logged_client:
        auth_section = f"""
        <div class="card-3d" style="display:flex; justify-content:space-between; align-items:center;">
          <div><b>Llogaria:</b> {logged_client["instagram"] or logged_client["email"]}</div>
          <form method="POST" style="margin:0;"><input type="hidden" name="form_type" value="client_logout"><button class="btn-sm-3d btn-del">Dil</button></form>
        </div>
        """
    else:
        auth_section = """
        <div class="card-3d">
          <h3>Regjistrohu / Kyçu</h3>
          <form method="POST">
            <input type="hidden" name="form_type" value="client_register">
            <label>Instagram / Email</label><input type="text" name="reg_instagram" required>
            <label>Telefoni</label><input type="tel" name="reg_phone" required>
            <label>Fjalëkalimi</label><input type="password" name="reg_password" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%;">Krijo Llogarinë</button>
          </form>
        </div>
        """

    inner = f"""
      <header><h2>GRANDE STUDIO</h2></header>
      <div class="container">
        {alert_html}
        {auth_section}
        <div class="card-3d">
          <h3>Rezervo Terminin</h3>
          <form method="POST">
            <label>Shërbimi</label><select name="service_id" id="service_id" required>{service_options}</select>
            <label>Data</label><input type="date" name="appt_date" id="appt_date" value="{today_str}" min="{today_str}" required>
            <label>Orari i Lirë</label><div id="slotsContainer" style="display:flex; flex-wrap:wrap; gap:4px; margin-top:5px;"></div>
            <input type="hidden" name="start_time" id="start_time" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:10px;">Konfirmo</button>
          </form>
        </div>
      </div>
      <script>
        const svcEl = document.getElementById('service_id');
        const dateEl = document.getElementById('appt_date');
        const slotsEl = document.getElementById('slotsContainer');
        const startInput = document.getElementById('start_time');

        async function loadSlots() {
          if (!svcEl.value) return;
          const res = await fetch(`/api/slots?date=${{dateEl.value}}&service_id=${{svcEl.value}}`);
          const data = await res.json();
          slotsEl.innerHTML = '';
          (data.slots || []).forEach(slot => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn-3d';
            btn.style.padding = '6px 10px';
            btn.style.fontSize = '0.85rem';
            btn.textContent = slot;
            btn.onclick = () => {
              document.querySelectorAll('#slotsContainer button').forEach(b => b.style.background = '');
              btn.style.background = '#b81d3d';
              btn.style.color = '#fff';
              startInput.value = slot;
            };
            slotsEl.appendChild(btn);
          });
        }
        svcEl.addEventListener('change', loadSlots);
        dateEl.addEventListener('change', loadSlots);
        loadSlots();
      </script>
    """
    return render_iphone_shell("Grande Studio", inner)


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel():
    init_db()
    conn = get_db()
    cur = conn.cursor()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "set_status":
            appt_id = request.form.get("appt_id", type=int)
            new_status = request.form.get("status", "Konfirmuar")
            cur.execute("SELECT client_id FROM appointments WHERE id = ?", (appt_id,))
            row_a = cur.fetchone()
            c_id = row_a["client_id"] if row_a else None
            cur.execute("UPDATE appointments SET status = ?, is_new = 0 WHERE id = ?", (new_status, appt_id))
            conn.commit()
            notify_client_appointment_decision(c_id, accepted=(new_status == "Konfirmuar"))

    cur.execute("SELECT a.*, s.name AS service_name FROM appointments a LEFT JOIN services s ON a.service_id = s.id ORDER BY a.id DESC")
    appts = cur.fetchall()
    conn.close()

    rows_html = "".join(f"""
        <tr>
          <td><b>{a["appt_date"]}</b><br>{a["start_time"]}</td>
          <td><b>{a["client_name"]}</b><br>{a["client_phone"]}</td>
          <td>{a["service_name"]}</td>
          <td><span class="badge {"b-ok" if a["status"]=="Konfirmuar" else "b-can"}">{a["status"]}</span></td>
          <td>
            <form method="POST" style="display:inline;"><input type="hidden" name="action" value="set_status"><input type="hidden" name="appt_id" value="{a["id"]}"><input type="hidden" name="status" value="Konfirmuar"><button class="btn-sm-3d btn-seen">Prano</button></form>
            <form method="POST" style="display:inline;"><input type="hidden" name="action" value="set_status"><input type="hidden" name="appt_id" value="{a["id"]}"><input type="hidden" name="status" value="Anuluar"><button class="btn-sm-3d btn-del">Anulo</button></form>
          </td>
        </tr>
    """ for a in appts)

    inner = f"""
      <header><h2>Admin — Grande Studio</h2><a href="/" style="color:#fff; font-size:0.8rem;">Klienti</a></header>
      <div class="container">
        <div class="card-3d">
          <h3>Terminet</h3>
          <table>
            <thead><tr><th>Data/Ora</th><th>Klienti</th><th>Shërbimi</th><th>Statusi</th><th>Veprime</th></tr></thead>
            <tbody>{rows_html if rows_html else '<tr><td colspan="5">Nuk ka termine.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    """
    return render_iphone_shell("Admin", inner)


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)