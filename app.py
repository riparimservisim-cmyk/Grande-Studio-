import base64
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

try:
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid
    from cryptography.hazmat.primitives import serialization
    HAS_WEBPUSH = True
except ImportError:
    HAS_WEBPUSH = False

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
MSG_RESET_PWD = "Admini ka kërkuar resetimin e fjalëkalimit tuaj. Vendosni një fjalëkalim të ri."

app = Flask(__name__, static_folder=STATIC_DIR)
app.secret_key = os.environ.get("SECRET_KEY") or "grande_studio_persistent_secret_key_2026"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 315360000

ADMIN_REMEMBER_COOKIE = "grande_admin_remember"
CLIENT_REMEMBER_COOKIE = "grande_client_remember"
COOKIE_MAX_AGE = 315360000

DB_PATH = os.path.join(DATA_DIR, "grande_studio.db")
VAPID_KEYS_PATH = os.path.join(DATA_DIR, "vapid_keys.json")

WORK_START = "09:00"
WORK_END = "18:30"
SLOT_STEP_MIN = 30

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or "noreply@grandestudio.local")

FAILED_LOGIN_TRACKER: dict[str, list[float]] = {}
MAX_FAILED_ATTEMPTS = 5
LOCK_WINDOW_SECONDS = 300


def get_or_create_vapid_keys() -> tuple[str, str]:
    env_pub = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    env_priv = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if env_pub and env_priv:
        return env_pub, env_priv

    if os.path.exists(VAPID_KEYS_PATH):
        try:
            with open(VAPID_KEYS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("public_key") and data.get("private_key"):
                    return data["public_key"], data["private_key"]
        except Exception:
            pass

    if HAS_WEBPUSH:
        try:
            vapid = Vapid()
            vapid.generate_keys()
            raw_pub = vapid.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint
            )
            pub_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode("ascii")
            priv_pem = vapid.private_pem().decode("utf-8")
            with open(VAPID_KEYS_PATH, "w", encoding="utf-8") as f:
                json.dump({"public_key": pub_b64, "private_key": priv_pem}, f)
            return pub_b64, priv_pem
        except Exception as e:
            print(f"[VAPID] Gabim gjatë gjenerimit të çelësave: {e}")

    return "", ""


VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY = get_or_create_vapid_keys()


def get_asset_version() -> str:
    mtimes = [SCRIPT_MTIME]
    for name in (
        "ig_avatar_clean.png",
        "ig_avatar_clean_2.png",
        "ig_avatar_clean_3.png",
        "ig_avatar_clean_4.png",
        "icon-192.png",
        "icon-512.png",
        "icon-512.jpg",
    ):
        p = os.path.join(STATIC_DIR, name)
        if os.path.exists(p):
            mtimes.append(os.path.getmtime(p))
    return str(int(max(mtimes)))


def normalize_ig_handle(raw: str) -> str:
    cleaned = (raw or "").strip()
    cleaned = re.sub(r"^https?://(www\.)?instagram\.com/", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip("/").lstrip("@").strip()
    return f"@{cleaned}" if cleaned else ""


def is_login_locked(client_ip: str) -> tuple[bool, int]:
    now = time.time()
    attempts = [t for t in FAILED_LOGIN_TRACKER.get(client_ip, []) if now - t < LOCK_WINDOW_SECONDS]
    FAILED_LOGIN_TRACKER[client_ip] = attempts
    if len(attempts) >= MAX_FAILED_ATTEMPTS:
        remaining = int(LOCK_WINDOW_SECONDS - (now - attempts[0]))
        return True, max(1, remaining)
    return False, 0


def record_failed_login(client_ip: str) -> None:
    now = time.time()
    attempts = [t for t in FAILED_LOGIN_TRACKER.get(client_ip, []) if now - t < LOCK_WINDOW_SECONDS]
    attempts.append(now)
    FAILED_LOGIN_TRACKER[client_ip] = attempts


def clear_failed_login(client_ip: str) -> None:
    FAILED_LOGIN_TRACKER.pop(client_ip, None)


def prepare_clean_nails_asset() -> str | None:
    for preferred in ("thonjet_clean_3.png", "thonjet_clean_2.png", "thonjet_clean.png"):
        p = os.path.join(STATIC_DIR, preferred)
        if os.path.exists(p):
            return f"/static/{preferred}"

    clean_path = os.path.join(STATIC_DIR, "thonjet_clean.png")
    candidates = [
        os.path.join(BASE_DIR, "thonjët.jpg"),
        os.path.join(BASE_DIR, "Thonjët.jpg"),
        os.path.join(BASE_DIR, "thonjet.jpg"),
        os.path.join(BASE_DIR, "Thonjet.jpg"),
        os.path.join(BASE_DIR, "thonjët.png"),
        os.path.join(BASE_DIR, "thonjet.png"),
        os.path.join(STATIC_DIR, "thonjët.jpg"),
        os.path.join(STATIC_DIR, "Thonjët.jpg"),
        os.path.join(STATIC_DIR, "thonjet.jpg"),
    ]

    src_file = next((p for p in candidates if os.path.exists(p)), None)
    if not src_file:
        return None

    if HAS_PIL:
        try:
            img = Image.open(src_file).convert("RGBA")
            w, h = img.size
            pixels = list(img.getdata())

            grande_pink = (242, 118, 224)
            pure_white = (255, 255, 255)
            subtext_y_start = int(h * 0.72)

            new_pixels = []
            for idx, (r, g, b, a) in enumerate(pixels):
                y = idx // w
                if r >= 238 and g >= 238 and b >= 238:
                    new_pixels.append((255, 255, 255, 0))
                    continue
                min_c = min(r, g, b)
                if min_c >= 218:
                    alpha = int(max(0, min(255, (238 - min_c) * (255 / 20))))
                    new_pixels.append((255, 255, 255, alpha))
                    continue
                luma = 0.299 * r + 0.587 * g + 0.114 * b
                strength = max(0.0, min(1.0, (238.0 - luma) / 145.0))
                alpha = int(max(45, min(255, strength * 255)))
                if y >= subtext_y_start and luma < 215:
                    new_pixels.append((pure_white[0], pure_white[1], pure_white[2], max(210, alpha)))
                else:
                    is_pinkish = (b > r + 10) or (r > 155 and b > 125 and g < 185)
                    if is_pinkish:
                        nr = min(255, int(r * 0.45 + grande_pink[0] * 0.65))
                        ng = min(255, int(g * 0.45 + grande_pink[1] * 0.65))
                        nb = min(255, int(b * 0.45 + grande_pink[2] * 0.65))
                    else:
                        nr = int(grande_pink[0] * (0.65 + 0.35 * strength))
                        ng = int(grande_pink[1] * (0.65 + 0.35 * strength))
                        nb = int(grande_pink[2] * (0.65 + 0.35 * strength))
                    new_pixels.append((min(255, nr), min(255, ng), min(255, nb), max(190, alpha)))

            img.putdata(new_pixels)
            bbox = img.getbbox()
            if bbox:
                img = img.crop(bbox)
            img = img.filter(ImageFilter.SHARPEN)
            img.save(clean_path, "PNG")
            return "/static/thonjet_clean.png"
        except Exception:
            pass
    return None


def prepare_ig_avatar_asset() -> str | None:
    for preferred in (
        "ig_avatar_clean_4.png",
        "ig_avatar_clean_3.png",
        "ig_avatar_clean_2.png",
        "ig_avatar_clean.png",
        "icon-512.jpg",
        "icon-192.png",
    ):
        p = os.path.join(STATIC_DIR, preferred)
        if os.path.exists(p):
            return f"/static/{preferred}"
    return None


def prepare_app_icons() -> None:
    icon_192 = os.path.join(STATIC_DIR, "icon-192.png")
    icon_512 = os.path.join(STATIC_DIR, "icon-512.png")
    ig_clean_1 = os.path.join(STATIC_DIR, "ig_avatar_clean.png")

    logo_sources = [icon_512, icon_192, ig_clean_1]
    chosen_logo = next((p for p in logo_sources if os.path.exists(p)), None)
    if not chosen_logo or not HAS_PIL:
        return

    try:
        src = Image.open(chosen_logo).convert("RGB")
        w, h = src.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        square = src.crop((left, top, left + side, top + side))

        if not os.path.exists(icon_192):
            square.resize((192, 192), Image.Resampling.LANCZOS).save(icon_192, "PNG", optimize=True)
        if not os.path.exists(icon_512):
            square.resize((512, 512), Image.Resampling.LANCZOS).save(icon_512, "PNG", optimize=True)
    except Exception as e:
        print(f"[ICON] Gabim gjatë përgatitjes së ikonave: {e}")


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

    # Tabelat për Web Push (PWA) me endpoint dhe subscription_json (përputhen me grande_studio.db)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT UNIQUE NOT NULL,
            subscription_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            endpoint TEXT UNIQUE NOT NULL,
            subscription_json TEXT NOT NULL,
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


def create_admin_remember_token() -> str:
    token = secrets.token_urlsafe(48)
    conn = get_db()
    conn.execute("INSERT INTO admin_sessions (token, created_at) VALUES (?, ?)", (token, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return token


def verify_admin_remember_token(token: str) -> bool:
    if not token:
        return False
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT token FROM admin_sessions WHERE token = ?", (token,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def delete_admin_remember_token(token: str) -> None:
    if not token:
        return
    conn = get_db()
    conn.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def create_client_remember_token(client_id: int) -> str:
    token = secrets.token_urlsafe(48)
    conn = get_db()
    conn.execute("INSERT INTO client_sessions (token, client_id, created_at) VALUES (?, ?, ?)", (token, client_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return token


def verify_client_remember_token(token: str) -> int | None:
    if not token:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT client_id FROM client_sessions WHERE token = ?", (token,))
    row = cur.fetchone()
    conn.close()
    return row["client_id"] if row else None


def delete_client_remember_token(token: str) -> None:
    if not token:
        return
    conn = get_db()
    conn.execute("DELETE FROM client_sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def _send_web_push(subs_rows, title: str, body: str, url: str = "/") -> None:
    if not HAS_WEBPUSH or not VAPID_PRIVATE_KEY or not subs_rows:
        return

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "icon": "/static/icon-192.png",
        "badge": "/static/icon-192.png"
    })

    dead_endpoints = []
    for row in subs_rows:
        try:
            sub_info = json.loads(row["subscription_json"])
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": "mailto:admin@grandestudio.local"},
                ttl=86400
            )
        except WebPushException as ex:
            if ex.response is not None and ex.response.status_code in (404, 410):
                dead_endpoints.append(row["endpoint"])
        except Exception as e:
            print(f"[WEBPUSH] Gabim gjatë dërgimit: {e}")

    if dead_endpoints:
        conn = get_db()
        cur = conn.cursor()
        for ep in dead_endpoints:
            cur.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (ep,))
            cur.execute("DELETE FROM client_push_subscriptions WHERE endpoint = ?", (ep,))
        conn.commit()
        conn.close()


def send_push_to_admins(title: str, body: str, url: str = "/admin") -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO admin_notifications (title, body, is_read, created_at) VALUES (?, ?, 0, ?)",
        (title, body, datetime.now().isoformat())
    )
    cur.execute("SELECT endpoint, subscription_json FROM push_subscriptions")
    subs = cur.fetchall()
    conn.commit()
    conn.close()
    _send_web_push(subs, title, body, url=url)


def notify_client_message(client_id: int | None, title: str, body: str, event_type: str = "info") -> None:
    if not client_id:
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO client_notifications (client_id, title, body, event_type, is_read, created_at) VALUES (?, ?, ?, ?, 0, ?)",
        (client_id, title, body, event_type, datetime.now().isoformat()),
    )
    cur.execute("SELECT endpoint, subscription_json FROM client_push_subscriptions WHERE client_id = ?", (client_id,))
    subs = cur.fetchall()
    conn.commit()
    conn.close()
    _send_web_push(subs, title, body, url="/")


def notify_client_appointment_decision(client_id: int | None, accepted: bool) -> None:
    if not client_id:
        return
    title = "Grande Studio — Termini u Pranua ✅" if accepted else "Grande Studio — Njoftim për Terminin"
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
    if cookie_tok and verify_admin_remember_token(cookie_tok):
        session["admin_logged_in"] = True
        session["admin_token"] = cookie_tok
        session.permanent = True
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

    if ig_avatar_url:
        ig_top_html = f'<img src="{ig_avatar_url}?v={v}" alt="Instagram Grande Studio" class="ig-banner-avatar">'
    else:
        ig_top_html = '<div class="ig-banner-avatar ig-fallback-badge" aria-hidden="true">IG</div>'

    if nails_url:
        nails_bottom_html = f'<img src="{nails_url}?v={v}" alt="Grande Studio Nails" class="nails-banner-bottom">'
    else:
        nails_bottom_html = """
        <svg class="nails-banner-bottom" viewBox="0 0 210 68" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <text x="8" y="34" fill="#f276e0" font-size="17" font-weight="900" letter-spacing="0.5">GRANDE STUDIO</text>
          <text x="8" y="52" fill="#ffffff" font-size="10.5" font-weight="800" letter-spacing="1.1">NAILS - BEAUTY - CARE</text>
        </svg>
        """
    return f"{ig_top_html}\n{nails_bottom_html}"


def get_ig_nav_button_html() -> str:
    return f"""
    <a href="{IG_WEB_URL}"
       data-ig-app="{IG_APP_URL}"
       data-ig-web="{IG_WEB_URL}"
       onclick="openInstagramProfile(event)"
       class="btn-3d btn-ig-3d"
       title="Hap Instagramin @{IG_USERNAME}">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="2" y="2" width="20" height="20" rx="5" ry="5"></rect>
        <path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"></path>
        <line x1="17.5" y1="6.5" x2="17.51" y2="6.5"></line>
      </svg>
      <span>IG</span>
    </a>
    """


BASE_STYLE = """
<style>
  :root {
    --vishnje-dark: #45030f;
    --vishnje: #72091d;
    --vishnje-mid: #96112b;
    --vishnje-bright: #b81d3d;
    --vishnje-soft: #f9ecef;
    --bg: #fdf6f7;
    --card: #ffffff;
    --text: #261217;
    --muted: #6e4e57;
    --border: #edd3da;
    --ok: #1f7a3d;
    --warn: #b41e2d;
  }
  * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif; }
  body {
    margin: 0; min-height: 100vh; font-size: 16px; color: var(--text);
    background-color: #26030a;
    background-image: radial-gradient(circle at 18% 20%, rgba(184, 29, 61, 0.42), transparent 45%), radial-gradient(circle at 82% 80%, rgba(150, 17, 43, 0.48), transparent 50%);
    display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 18px 12px;
  }
  .studio-stage-label { color: #ffdce4; font-size: 0.9rem; letter-spacing: 0.5px; margin-bottom: 10px; text-align: center; opacity: 0.9; }
  .iphone-chassis {
    width: 412px; max-width: 100%; height: 852px; max-height: calc(100vh - 64px);
    background: #141112; border-radius: 52px; padding: 13px;
    box-shadow: 0 0 0 2px #5e2430, 0 0 0 5px #24090f, 0 28px 65px rgba(0, 0, 0, 0.65);
    position: relative; display: flex; flex-direction: column;
  }
  .iphone-screen {
    flex: 1; border-radius: 40px; overflow: hidden; display: flex; flex-direction: column; position: relative;
    background-color: #fdf5f7;
  }
  .ios-status-bar {
    background: linear-gradient(180deg, #4f0413, #630719); color: #fff;
    padding: 10px 22px 6px; display: flex; justify-content: space-between; align-items: center;
    font-size: 0.82rem; font-weight: 700; flex-shrink: 0;
  }
  .dynamic-island { width: 96px; height: 24px; background: #090708; border-radius: 999px; display: flex; align-items: center; justify-content: flex-end; padding-right: 9px; }
  .dynamic-island-cam { width: 9px; height: 9px; border-radius: 50%; background: radial-gradient(circle, #283b63 25%, #0d1322 70%); }
  .app-scroll-area { flex: 1; overflow-y: auto; overflow-x: hidden; -webkit-overflow-scrolling: touch; }
  .ios-home-indicator { height: 20px; background: rgba(253, 245, 247, 0.92); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
  .ios-home-indicator span { width: 124px; height: 4px; background: #3b1922; border-radius: 999px; opacity: 0.45; }

  @media (max-width: 520px), (display-mode: standalone) {
    body { padding: 0; background: var(--bg); display: block; }
    .studio-stage-label, .ios-status-bar, .ios-home-indicator { display: none !important; }
    .iphone-chassis { width: 100%; height: auto; min-height: 100vh; max-height: none; border-radius: 0; padding: 0; background: transparent; box-shadow: none; }
    .iphone-screen { border-radius: 0; overflow: visible; min-height: 100vh; }
    .app-scroll-area { overflow: visible; }
    header { padding-top: calc(12px + env(safe-area-inset-top)); }
    .container { padding-bottom: calc(28px + env(safe-area-inset-top)); }
  }

  header {
    background: linear-gradient(145deg, var(--vishnje-bright), var(--vishnje) 55%, var(--vishnje-dark));
    color: #fff; padding: 12px 12px; position: sticky; top: 0; z-index: 30;
    box-shadow: 0 5px 0 var(--vishnje-dark), 0 10px 20px rgba(74, 4, 17, 0.25);
  }
  .header-inner { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
  .brand-title { margin: 0; font-size: 1.04rem; letter-spacing: 0.4px; font-weight: 800; white-space: nowrap; }
  .brand-sub { margin: 2px 0 0; font-size: 0.74rem; color: #ffd6df; white-space: nowrap; }
  .btn-3d {
    display: inline-flex; align-items: center; justify-content: center; gap: 4px; border: none; border-radius: 11px;
    font-weight: 700; cursor: pointer; text-decoration: none; transition: transform 0.12s ease;
  }
  .btn-3d:active { transform: translateY(3px); }
  .btn-vishnje-3d {
    background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje)); color: #fff;
    padding: 11px 15px; font-size: 0.96rem; box-shadow: 0 4px 0 var(--vishnje-dark), 0 7px 14px rgba(114, 9, 29, 0.26);
  }
  .btn-ghost-3d {
    background: rgba(255, 255, 255, 0.15); color: #fff; padding: 6px 8px; font-size: 0.78rem;
    border: 1px solid rgba(255, 255, 255, 0.28); box-shadow: 0 3px 0 rgba(45, 2, 10, 0.65);
  }
  .btn-ig-3d {
    background: linear-gradient(135deg, #f58529, #dd2a7b 55%, #8134af); color: #fff; padding: 6px 9px; font-size: 0.8rem;
    border: 1px solid rgba(255, 255, 255, 0.42); box-shadow: 0 3px 0 rgba(45, 2, 10, 0.7);
  }
  .container { padding: 13px 12px 28px; }
  .combo-banner {
    background: linear-gradient(135deg, #47020e 0%, #7a0a1f 55%, #a81632 100%);
    color: #fff; border-radius: 17px; padding: 13px 13px 16px; margin-bottom: 13px;
    position: relative; overflow: hidden; min-height: 150px;
    box-shadow: 0 5px 0 var(--vishnje-dark), 0 12px 22px rgba(114, 9, 29, 0.22);
  }
  .ig-banner-avatar {
    position: absolute; right: 20px; top: 12px; width: 68px; height: 68px; border-radius: 50%;
    object-fit: cover; border: 2px solid rgba(255, 255, 255, 0.88); z-index: 2;
  }
  .nails-banner-bottom { position: absolute; right: 4px; bottom: 10px; width: 145px; height: auto; z-index: 1; }
  .combo-banner-content { position: relative; z-index: 3; max-width: 65%; }
  .combo-banner h3 { margin: 0 0 4px; font-size: 1.04rem; font-weight: 800; }
  .combo-banner p { margin: 0 0 10px; font-size: 0.84rem; line-height: 1.38; }

  .card-3d {
    background: rgba(255, 255, 255, 0.96); border: 1px solid var(--border); border-radius: 16px;
    padding: 14px; margin-bottom: 13px; box-shadow: 0 5px 0 #e6cbd2, 0 10px 20px rgba(74, 4, 17, 0.07);
  }
  .push-banner-card {
    background: linear-gradient(135deg, #fff5f7, #ffe3ea);
    border: 1.5px solid var(--vishnje-bright);
    border-radius: 14px; padding: 11px 12px; margin-bottom: 12px;
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
  }
  .auth-mode-switch { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-bottom: 10px; }
  .auth-mode-btn {
    padding: 8px; border-radius: 10px; border: 1px solid var(--border); background: #fff;
    color: var(--vishnje); font-size: 0.86rem; font-weight: 800; cursor: pointer;
  }
  .auth-mode-btn.active { background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje)); color: #fff; }
  .client-session-bar {
    display: flex; justify-content: space-between; align-items: center; gap: 8px;
    background: #fff3f6; border: 1px solid #f2c4cf; border-radius: 12px; padding: 9px 11px;
    margin-bottom: 10px; font-size: 0.84rem; color: var(--vishnje-dark);
  }
  h2 { margin: 0 0 9px; font-size: 1.08rem; color: var(--vishnje); }
  label { display: block; font-size: 0.88rem; font-weight: 700; margin: 10px 0 4px; color: var(--vishnje-dark); }
  input, select, textarea {
    width: 100%; padding: 10px 11px; border: 1.5px solid #e2c2ca; border-radius: 10px;
    font-size: 0.95rem; background: #fffdfd; color: var(--text);
  }
  .slots { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .slot-3d {
    padding: 8px 11px; border-radius: 9px; border: 1px solid #dfb9c3;
    background: linear-gradient(180deg, #ffffff, #fdf0f3); color: var(--vishnje);
    font-size: 0.88rem; font-weight: 700; cursor: pointer; box-shadow: 0 3px 0 #d6aeb8;
  }
  .slot-3d.active { background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje)); color: #fff; }
  .alert { padding: 10px 12px; border-radius: 12px; margin-bottom: 11px; font-size: 0.9rem; font-weight: 600; }
  .alert-ok { background: #ebfaef; color: var(--ok); border: 1px solid #a8e4ba; }
  .alert-err { background: #fff0f2; color: var(--warn); border: 1px solid #f7bcc4; }
  .live-toast-banner {
    position: fixed; top: 14px; left: 50%; transform: translateX(-50%); width: 92%; max-width: 390px;
    background: linear-gradient(135deg, #9f1239, #e11d48); color: #fff; padding: 14px; border-radius: 16px;
    z-index: 9999; box-shadow: 0 15px 35px rgba(159, 18, 57, 0.55); border: 2px solid #fda4af; display: none;
  }
  table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
  th, td { padding: 7px 5px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
  th { color: var(--vishnje); font-size: 0.78rem; text-transform: uppercase; }
  .badge { padding: 2px 7px; border-radius: 999px; font-size: 0.76rem; font-weight: 800; }
  .b-ok { background: #d3f9d8; color: #1f7a3d; }
  .b-can { background: #ffe3e3; color: #b41e2d; }
  .btn-sm-3d {
    padding: 6px 9px; border-radius: 8px; border: none; cursor: pointer;
    font-size: 0.8rem; font-weight: 700; box-shadow: 0 2px 0 rgba(0,0,0,0.18); margin-bottom: 3px;
  }
  .btn-del { background: #ffe3e3; color: #b41e2d; }
  .btn-seen { background: #dcfce7; color: #166534; }
</style>
"""


def render_iphone_shell(title: str, inner_html: str) -> str:
    now_clock = datetime.now().strftime("%H:%M")
    v = get_asset_version()
    return f"""
    <!doctype html>
    <html lang="sq" translate="no">
    <head>
      <meta charset="utf-8">
      <meta name="google" content="notranslate">
      <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
      <title>{title}</title>
      <meta name="theme-color" content="#72091d">
      <meta name="apple-mobile-web-app-capable" content="yes">
      <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
      <meta name="apple-mobile-web-app-title" content="Grande Studio">
      <link rel="manifest" href="/manifest.json?v={v}">
      <link rel="apple-touch-icon" sizes="192x192" href="/static/icon-192.png?v={v}">
      <link rel="icon" type="image/png" sizes="192x192" href="/static/icon-192.png?v={v}">
      {BASE_STYLE}
    </head>
    <body class="notranslate">
      <div id="liveToastBanner" class="live-toast-banner" onclick="this.style.display='none'">
        <div id="liveToastTitle" style="font-weight:900; font-size:0.95rem; margin-bottom:3px;"></div>
        <div id="liveToastBody" style="font-size:0.85rem;"></div>
      </div>

      <div class="studio-stage-label">📱 Grande Studio — Thonj • Qerpikë • Vetulla</div>
      <div class="iphone-chassis">
        <div class="iphone-screen">
          <div class="ios-status-bar">
            <span>{now_clock}</span>
            <div class="dynamic-island"><span class="dynamic-island-cam"></span></div>
            <span>5G 100%</span>
          </div>
          <div class="app-scroll-area" id="iphoneScrollArea">
            {inner_html}
          </div>
          <div class="ios-home-indicator"><span></span></div>
        </div>
      </div>

      <script>
        const VAPID_PUBLIC_KEY = "{VAPID_PUBLIC_KEY}";

        function urlBase64ToUint8Array(base64String) {{
          const padding = '='.repeat((4 - base64String.length % 4) % 4);
          const base64 = (base64String + padding).replace(/\\-/g, '+').replace(/_/g, '/');
          const rawData = window.atob(base64);
          const outputArray = new Uint8Array(rawData.length);
          for (let i = 0; i < rawData.length; ++i) {{
            outputArray[i] = rawData.charCodeAt(i);
          }}
          return outputArray;
        }}

        function playNotificationChime() {{
          try {{
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            const ctx = new AudioCtx();
            if (ctx.state === 'suspended') ctx.resume();
            const notes = [587.33, 880.00, 1174.66, 880.00, 1174.66];
            notes.forEach((freq, idx) => {{
              const osc = ctx.createOscillator();
              const gain = ctx.createGain();
              osc.type = 'triangle';
              osc.frequency.setValueAtTime(freq, ctx.currentTime + idx * 0.14);
              gain.gain.setValueAtTime(0.001, ctx.currentTime + idx * 0.14);
              gain.gain.exponentialRampToValueAtTime(0.45, ctx.currentTime + idx * 0.14 + 0.03);
              gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + idx * 0.14 + 0.45);
              osc.connect(gain);
              gain.connect(ctx.destination);
              osc.start(ctx.currentTime + idx * 0.14);
              osc.stop(ctx.currentTime + idx * 0.14 + 0.46);
            }});
          }} catch (e) {{}}
        }}

        function showLiveToast(title, body) {{
          const banner = document.getElementById('liveToastBanner');
          document.getElementById('liveToastTitle').textContent = title;
          document.getElementById('liveToastBody').textContent = body;
          banner.style.display = 'block';
          playNotificationChime();
        }}

        async function enablePushNotifications() {{
          if (!('serviceWorker' in navigator) || !('PushManager' in window)) {{
            alert('Shfletuesi juaj nuk i mbështet njoftimet Push. Përdorni Chrome ose Safari (Add to Home Screen).');
            return;
          }}
          if (!VAPID_PUBLIC_KEY) {{
            alert('Çelësat VAPID nuk janë konfiguruar në server.');
            return;
          }}
          try {{
            const perm = await Notification.requestPermission();
            if (perm !== 'granted') {{
              alert('Duhet të lejoni njoftimet (Allow) në shfletues që t\\'ju vijnë mesazhet.');
              return;
            }}
            const reg = await navigator.serviceWorker.ready;
            const sub = await reg.pushManager.subscribe({{
              userVisibleOnly: true,
              applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY)
            }});
            const isAdmin = window.location.pathname.startsWith('/admin');
            const endpointUrl = isAdmin ? '/api/push/subscribe' : '/api/client/push/subscribe';
            const res = await fetch(endpointUrl, {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify(sub.toJSON())
            }});
            if (res.ok) {{
              playNotificationChime();
              const btnBox = document.getElementById('pushPromptBox');
              if (btnBox) btnBox.style.display = 'none';
              alert('✅ Njoftimet u aktivizuan me sukses në këtë pajisje!');
            }} else {{
              alert('Kyçuni në llogari fillimisht për të lidhur njoftimet.');
            }}
          }} catch (err) {{
            alert('Gabim gjatë aktivizimit të njoftimeve: ' + err.message);
          }}
        }}

        function openInstagramProfile(ev) {{
          if (ev) ev.preventDefault();
          const el = ev && ev.currentTarget ? ev.currentTarget : null;
          const appUrl = el ? el.getAttribute('data-ig-app') : '{IG_APP_URL}';
          const webUrl = el ? el.getAttribute('data-ig-web') : '{IG_WEB_URL}';
          const start = Date.now();
          window.location.href = appUrl;
          setTimeout(function() {{
            if (Date.now() - start < 1600) {{
              window.open(webUrl, '_blank', 'noopener,noreferrer') || (window.location.href = webUrl);
            }}
          }}, 650);
        }}

        // Regjistrimi automatik i Service Worker-it për PWA dhe Push
        if ('serviceWorker' in navigator) {{
          window.addEventListener('load', async () => {{
            try {{
              const reg = await navigator.serviceWorker.register('/sw.js');
              if (Notification.permission === 'granted' && VAPID_PUBLIC_KEY) {{
                const existingSub = await reg.pushManager.getSubscription();
                if (existingSub) {{
                  const isAdmin = window.location.pathname.startsWith('/admin');
                  const endpointUrl = isAdmin ? '/api/push/subscribe' : '/api/client/push/subscribe';
                  fetch(endpointUrl, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(existingSub.toJSON())
                  }}).catch(() => {{}});
                  const btnBox = document.getElementById('pushPromptBox');
                  if (btnBox) btnBox.style.display = 'none';
                }}
              }}
            }} catch (e) {{}}
          }});
        }}

        // Kontroll periodik për njoftime të reja kur aplikacioni është hapur
        setInterval(async () => {{
          const isAdmin = window.location.pathname.startsWith('/admin');
          if (isAdmin && !window.location.pathname.includes('login')) {{
            try {{
              const res = await fetch('/api/admin/notifications');
              if (res.ok) {{
                const data = await res.json();
                if (data.notifications && data.notifications.length > 0) {{
                  const n = data.notifications[0];
                  showLiveToast(n.title, n.body);
                  await fetch('/api/admin/notifications/read', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ id: n.id }})
                  }});
                }}
              }}
            }} catch (e) {{}}
          }} else if (!isAdmin) {{
            try {{
              const res = await fetch('/api/client/notifications');
              if (res.ok) {{
                const data = await res.json();
                if (data.notifications && data.notifications.length > 0) {{
                  const n = data.notifications[0];
                  showLiveToast(n.title, n.body);
                  await fetch('/api/client/notifications/read', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ id: n.id }})
                  }});
                }}
              }}
            }} catch (e) {{}}
          }}
        }}, 7000);
      </script>
    </body>
    </html>
    """


@app.after_request
def apply_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/manifest.json")
def pwa_manifest():
    v = get_asset_version()
    manifest = {
        "name": "Grande Studio — Thonj, Qerpikë & Vetulla",
        "short_name": "Grande Studio",
        "description": "Rezervime online për Thonj, Qerpikë dhe Vetulla te Grande Studio.",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#72091d",
        "theme_color": "#72091d",
        "orientation": "portrait",
        "icons": [
            {"src": f"/static/icon-192.png?v={v}", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": f"/static/icon-512.png?v={v}", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    }
    return Response(json.dumps(manifest), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    sw_code = """
    self.addEventListener('install', (event) => {
      self.skipWaiting();
    });

    self.addEventListener('activate', (event) => {
      event.waitUntil(clients.claim());
    });

    self.addEventListener('push', (event) => {
      let data = { title: 'Grande Studio', body: 'Keni një njoftim të ri!', url: '/' };
      if (event.data) {
        try {
          data = Object.assign(data, event.data.json());
        } catch (e) {
          data.body = event.data.text();
        }
      }
      const options = {
        body: data.body,
        icon: data.icon || '/static/icon-192.png',
        badge: data.badge || '/static/icon-192.png',
        vibrate: [200, 100, 200],
        data: { url: data.url || '/' }
      };
      event.waitUntil(self.registration.showNotification(data.title, options));
    });

    self.addEventListener('notificationclick', (event) => {
      event.notification.close();
      const targetUrl = (event.notification.data && event.notification.data.url) || '/';
      event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
          for (let client of windowClients) {
            if (client.url.includes(targetUrl) && 'focus' in client) {
              return client.focus();
            }
          }
          if (clients.openWindow) {
            return clients.openWindow(targetUrl);
          }
        })
      );
    });
    """
    return Response(sw_code, mimetype="application/javascript")


@app.route("/api/push/subscribe", methods=["POST"])
def api_push_subscribe():
    if not is_admin_authenticated():
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    if not endpoint:
        return jsonify({"ok": False}), 400

    conn = get_db()
    conn.execute(
        """
        INSERT INTO push_subscriptions (endpoint, subscription_json, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET subscription_json = excluded.subscription_json, created_at = excluded.created_at
        """,
        (endpoint, json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/client/push/subscribe", methods=["POST"])
def api_client_push_subscribe():
    client_id = session.get("client_id")
    if not client_id:
        cookie_tok = request.cookies.get(CLIENT_REMEMBER_COOKIE, "")
        client_id = verify_client_remember_token(cookie_tok)
        if client_id:
            session["client_id"] = client_id
    if not client_id:
        return jsonify({"ok": False}), 401

    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    if not endpoint:
        return jsonify({"ok": False}), 400

    conn = get_db()
    conn.execute(
        """
        INSERT INTO client_push_subscriptions (client_id, endpoint, subscription_json, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET client_id = excluded.client_id, subscription_json = excluded.subscription_json
        """,
        (client_id, endpoint, json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/client/notifications")
def api_client_notifications():
    client_id = session.get("client_id")
    if not client_id:
        return jsonify({"notifications": []})

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title, body, event_type, is_read, created_at FROM client_notifications WHERE client_id = ? AND is_read = 0 ORDER BY id DESC LIMIT 10",
        (client_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"notifications": rows})


@app.route("/api/client/notifications/read", methods=["POST"])
def api_client_notifications_read():
    client_id = session.get("client_id")
    if not client_id:
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    notif_id = data.get("id")
    conn = get_db()
    if notif_id:
        conn.execute("UPDATE client_notifications SET is_read = 1 WHERE client_id = ? AND id = ?", (client_id, notif_id))
    else:
        conn.execute("UPDATE client_notifications SET is_read = 1 WHERE client_id = ?", (client_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/notifications")
@admin_required
def api_admin_notifications():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, title, body, is_read, created_at FROM admin_notifications WHERE is_read = 0 ORDER BY id DESC LIMIT 15")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"notifications": rows})


@app.route("/api/admin/notifications/read", methods=["POST"])
@admin_required
def api_admin_notifications_read():
    data = request.get_json(silent=True) or {}
    notif_id = data.get("id")
    conn = get_db()
    if notif_id:
        conn.execute("UPDATE admin_notifications SET is_read = 1 WHERE id = ?", (notif_id,))
    else:
        conn.execute("UPDATE admin_notifications SET is_read = 1")
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
    return jsonify({"slots": slots, "duration_min": row["duration_min"]})


@app.route("/", methods=["GET", "POST"])
def index():
    init_db()
    prepare_clean_nails_asset()
    prepare_ig_avatar_asset()
    prepare_app_icons()
    banner_art_html = get_banner_artwork_html()
    ig_nav_html = get_ig_nav_button_html()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM services ORDER BY CASE category WHEN 'Kombinim' THEN 1 WHEN 'Thonj' THEN 2 WHEN 'Qerpikë' THEN 3 ELSE 4 END, id"
    )
    services = cur.fetchall()

    # Rikthimi automatik i sesionit të klientit nga cookie
    if not session.get("client_id"):
        cookie_tok = request.cookies.get(CLIENT_REMEMBER_COOKIE, "")
        c_id = verify_client_remember_token(cookie_tok)
        if c_id:
            session["client_id"] = c_id
            session.permanent = True

    msg = None
    msg_type = "ok"
    today_str = datetime.now().strftime("%Y-%m-%d")
    client_remember_token_to_set = None
    clear_client_cookie = False

    if request.method == "POST":
        form_type = request.form.get("form_type", "booking")

        if form_type == "client_register":
            raw_id = request.form.get("reg_identifier", "").strip()
            phone_in = request.form.get("reg_phone", "").strip()
            pwd_in = request.form.get("reg_password", "")
            is_email = "@" in raw_id
            reg_method = "email" if is_email else "instagram"
            ig_handle = "" if is_email else normalize_ig_handle(raw_id)
            email_in = raw_id.lower() if is_email else ""

            if not raw_id:
                msg = "Shkruani Instagramin ose Email-in."
                msg_type = "err"
            elif not phone_in or len(re.sub(r"\D", "", phone_in)) < 6:
                msg = "Numri i telefonit është i detyrueshëm."
                msg_type = "err"
            elif len(pwd_in) < 6:
                msg = "Fjalëkalimi duhet të ketë të paktën 6 karaktere."
                msg_type = "err"
            else:
                if is_email:
                    cur.execute("SELECT id FROM client_accounts WHERE LOWER(email) = ?", (email_in,))
                else:
                    cur.execute("SELECT id FROM client_accounts WHERE LOWER(instagram) = LOWER(?)", (ig_handle,))
                if cur.fetchone():
                    msg = "Kjo llogari ekziston. Zgjidhni 'Kyçu' më lart."
                    msg_type = "err"
                else:
                    cur.execute(
                        """
                        INSERT INTO client_accounts (auth_method, instagram, email, phone, password_hash, is_blocked, password_reset_required, created_at)
                        VALUES (?, ?, ?, ?, ?, 0, 0, ?)
                        """,
                        (reg_method, ig_handle, email_in, phone_in, generate_password_hash(pwd_in), datetime.now().isoformat()),
                    )
                    conn.commit()
                    new_client_id = cur.lastrowid
                    session["client_id"] = new_client_id
                    session.permanent = True
                    client_remember_token_to_set = create_client_remember_token(new_client_id)
                    msg = "Regjistrimi u krye me sukses!"

        elif form_type == "client_login":
            login_id = request.form.get("login_identifier", "").strip()
            login_pwd = request.form.get("login_password", "")
            ig_cand = normalize_ig_handle(login_id)
            email_cand = login_id.lower()
            cur.execute(
                "SELECT * FROM client_accounts WHERE LOWER(email) = ? OR (instagram != '' AND LOWER(instagram) = LOWER(?))",
                (email_cand, ig_cand),
            )
            acc_row = cur.fetchone()
            if acc_row and acc_row["is_blocked"] == 1:
                msg = "Llogaria juaj është e bllokuar."
                msg_type = "err"
            elif acc_row and check_password_hash(acc_row["password_hash"], login_pwd):
                session["client_id"] = acc_row["id"]
                session.permanent = True
                client_remember_token_to_set = create_client_remember_token(acc_row["id"])
                msg = "U kyçët me sukses!"
            else:
                msg = "Të dhënat e hyrjes janë të pasakta."
                msg_type = "err"

        elif form_type == "client_logout":
            delete_client_remember_token(request.cookies.get(CLIENT_REMEMBER_COOKIE, ""))
            session.pop("client_id", None)
            clear_client_cookie = True
            msg = "Jeni çkyçur."

        else:
            cur.execute("SELECT * FROM client_accounts WHERE id = ?", (session.get("client_id") or -1,))
            current_client = cur.fetchone()
            if not current_client:
                msg = "Ju lutem regjistrohuni ose kyçuni më parë për të rezervuar."
                msg_type = "err"
            else:
                client_name = current_client["instagram"] or current_client["email"]
                service_id = request.form.get("service_id", type=int)
                appt_date = request.form.get("appt_date", "").strip()
                start_time = request.form.get("start_time", "").strip()
                notes = request.form.get("notes", "").strip()

                cur.execute("SELECT * FROM services WHERE id = ?", (service_id,))
                svc = cur.fetchone()
                if svc and appt_date and start_time:
                    end_time = min_to_time(time_to_min(start_time) + svc["duration_min"])
                    cur.execute(
                        """
                        INSERT INTO appointments
                        (client_id, client_name, client_phone, client_instagram, client_email,
                         service_id, appt_date, start_time, end_time, notes, status, is_new, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Konfirmuar', 1, ?)
                        """,
                        (
                            current_client["id"], client_name, current_client["phone"],
                            current_client["instagram"], current_client["email"],
                            service_id, appt_date, start_time, end_time, notes, datetime.now().isoformat(),
                        ),
                    )
                    conn.commit()
                    send_push_to_admins(
                        title=f"🔔 Rezervim i Ri: {client_name}",
                        body=f"{svc['name']} • {appt_date} ora {start_time}",
                        url="/admin"
                    )
                    msg = "Rezervimi u dërgua me sukses! Do të merrni njoftim sapo të konfirmohet."
                else:
                    msg = "Zgjidhni shërbimin, datën dhe orarin e lirë."
                    msg_type = "err"

    logged_client = None
    client_appts = []
    if session.get("client_id"):
        cur.execute("SELECT * FROM client_accounts WHERE id = ?", (session["client_id"],))
        logged_client = cur.fetchone()
        if logged_client:
            cur.execute(
                """
                SELECT a.*, s.name AS service_name FROM appointments a
                LEFT JOIN services s ON a.service_id = s.id
                WHERE a.client_id = ? ORDER BY a.id DESC LIMIT 10
                """,
                (logged_client["id"],)
            )
            client_appts = cur.fetchall()
    conn.close()

    service_options = "\n".join(
        f'<option value="{s["id"]}">[{s["category"]}] {s["name"]} — {s["duration_min"]} min ({s["price"]:.0f}€)</option>'
        for s in services
    )

    alert_html = f'<div class="alert {"alert-ok" if msg_type == "ok" else "alert-err"}">{msg}</div>' if msg else ""

    if logged_client:
        push_box = """
        <div id="pushPromptBox" class="push-banner-card">
          <div style="font-size:0.83rem; color:#45030f;">
            <b>🔔 Merr njoftime në telefon</b><br>Njoftohu direkt kur konfirmohet termini yt.
          </div>
          <button type="button" onclick="enablePushNotifications()" class="btn-3d btn-vishnje-3d" style="padding:8px 11px; font-size:0.8rem;">Aktivizo</button>
        </div>
        """
        appts_list_html = "".join(
            f'<div style="padding:8px 0; border-bottom:1px solid #edd3da; font-size:0.85rem;"><b>{a["appt_date"]}</b> ora <b>{a["start_time"]}</b> — {a["service_name"]} <span class="badge {"b-ok" if a["status"]=="Konfirmuar" else "b-can"}">{a["status"]}</span></div>'
            for a in client_appts
        )
        my_appts_card = f'<div class="card-3d"><h2>📅 Terminet e Mia</h2>{appts_list_html}</div>' if appts_list_html else ""

        client_auth_section = f"""
        <div class="client-session-bar">
          <div><b>✅ Llogaria:</b> {logged_client["instagram"] or logged_client["email"]}</div>
          <form method="POST" style="margin:0;">
            <input type="hidden" name="form_type" value="client_logout">
            <button type="submit" class="btn-sm-3d btn-del">Dil</button>
          </form>
        </div>
        {push_box}
        {my_appts_card}
        """
    else:
        client_auth_section = """
        <div class="card-3d">
          <div class="auth-mode-switch">
            <button type="button" id="btnShowReg" class="auth-mode-btn active" onclick="toggleAuthBox('reg')">Regjistrohu</button>
            <button type="button" id="btnShowLog" class="auth-mode-btn" onclick="toggleAuthBox('log')">Kyçu</button>
          </div>
          <form method="POST" id="formReg">
            <input type="hidden" name="form_type" value="client_register">
            <label>Instagram (@emri) ose Email</label>
            <input type="text" name="reg_identifier" placeholder="@llogaria_ig ose email@..." required>
            <label>Telefoni</label>
            <input type="tel" name="reg_phone" placeholder="+383..." required>
            <label>Fjalëkalimi</label>
            <input type="password" name="reg_password" minlength="6" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:10px;">Krijo Llogarinë</button>
          </form>
          <form method="POST" id="formLog" style="display:none;">
            <input type="hidden" name="form_type" value="client_login">
            <label>Instagram ose Email</label>
            <input type="text" name="login_identifier" placeholder="@llogaria_ig ose email@..." required>
            <label>Fjalëkalimi</label>
            <input type="password" name="login_password" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:10px;">Kyçu</button>
          </form>
        </div>
        <script>
          function toggleAuthBox(mode) {
            document.getElementById('formReg').style.display = mode === 'reg' ? 'block' : 'none';
            document.getElementById('formLog').style.display = mode === 'log' ? 'block' : 'none';
            document.getElementById('btnShowReg').classList.toggle('active', mode === 'reg');
            document.getElementById('btnShowLog').classList.toggle('active', mode === 'log');
          }
        </script>
        """

    inner = f"""
      <header>
        <div class="header-inner">
          <div>
            <h1 class="brand-title">GRANDE STUDIO</h1>
            <p class="brand-sub">Thonj • Qerpikë • Vetulla</p>
          </div>
          <div class="header-mid">{ig_nav_html}</div>
          <div class="header-actions">
            <a href="{url_for('admin_panel')}" class="btn-3d btn-ghost-3d">🔒 Admin</a>
          </div>
        </div>
      </header>

      <div class="container">
        {alert_html}
        <div class="combo-banner">
          {banner_art_html}
          <div class="combo-banner-content">
            <h3>✨ Thonj, Qerpikë &amp; Vetulla</h3>
            <p>Trajtime dhe paketa të kombinuara te Grande Studio.</p>
          </div>
        </div>

        {client_auth_section}

        <div class="card-3d">
          <h2>Rezervo Terminin</h2>
          <form method="POST" id="bookingForm">
            <label>Shërbimi</label>
            <select name="service_id" id="service_id" required>{service_options}</select>
            <label>Data</label>
            <input type="date" name="appt_date" id="appt_date" value="{today_str}" min="{today_str}" required>
            <label>Orari i Lirë</label>
            <div id="slotsContainer" class="slots">Zgjidh datën...</div>
            <input type="hidden" name="start_time" id="start_time" required>
            <label>Shënim (opsionale)</label>
            <input type="text" name="notes" placeholder="p.sh. Ngjyrë e kuqe, dizajn...">
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:14px;">Konfirmo Terminin</button>
          </form>
        </div>
      </div>
      <script>
        const svcEl = document.getElementById('service_id');
        const dateEl = document.getElementById('appt_date');
        const slotsEl = document.getElementById('slotsContainer');
        const startInput = document.getElementById('start_time');

        async function loadSlots() {{
          if (!svcEl.value) return;
          const res = await fetch(`/api/slots?date=${{dateEl.value}}&service_id=${{svcEl.value}}`);
          const data = await res.json();
          slotsEl.innerHTML = '';
          startInput.value = '';
          (data.slots || []).forEach(slot => {{
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'slot-3d';
            btn.textContent = slot;
            btn.onclick = () => {{
              document.querySelectorAll('.slot-3d').forEach(b => b.classList.remove('active'));
              btn.classList.add('active');
              startInput.value = slot;
            }};
            slotsEl.appendChild(btn);
          }});
          if (!data.slots || data.slots.length === 0) {{
            slotsEl.textContent = 'Nuk ka orare të lira për këtë datë.';
          }}
        }}
        svcEl.addEventListener('change', loadSlots);
        dateEl.addEventListener('change', loadSlots);
        loadSlots();
      </script>
    """
    resp = Response(render_iphone_shell("Grande Studio", inner))
    if client_remember_token_to_set:
        resp.set_cookie(CLIENT_REMEMBER_COOKIE, client_remember_token_to_set, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax")
    elif clear_client_cookie:
        resp.delete_cookie(CLIENT_REMEMBER_COOKIE)
    return resp


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    init_db()
    acc = get_admin_account()
    err = None
    if request.method == "POST":
        email_in = request.form.get("email", "").strip().lower()
        pwd_in = request.form.get("password", "")
        if not acc:
            # Krijimi i llogarisë së parë admin nëse databaza është bosh
            conn = get_db()
            conn.execute(
                "INSERT INTO admin_account (id, email, password_hash, updated_at) VALUES (1, ?, ?, ?)",
                (email_in, generate_password_hash(pwd_in), datetime.now().isoformat())
            )
            conn.commit()
            conn.close()
            tok = create_admin_remember_token()
            session["admin_logged_in"] = True
            session.permanent = True
            resp = redirect(url_for("admin_panel"))
            resp.set_cookie(ADMIN_REMEMBER_COOKIE, tok, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax")
            return resp
        elif email_in == acc["email"].lower() and check_password_hash(acc["password_hash"], pwd_in):
            tok = create_admin_remember_token()
            session["admin_logged_in"] = True
            session.permanent = True
            resp = redirect(url_for("admin_panel"))
            resp.set_cookie(ADMIN_REMEMBER_COOKIE, tok, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax")
            return resp
        else:
            err = "Email ose fjalëkalim i pasaktë."

    err_html = f'<div class="alert alert-err">{err}</div>' if err else ""
    inner = f"""
      <header>
        <div class="header-inner">
          <div><h1 class="brand-title">GRANDE STUDIO</h1><p class="brand-sub">Hyrje Admin</p></div>
          <a href="{url_for('index')}" class="btn-3d btn-ghost-3d">← Kthehu</a>
        </div>
      </header>
      <div class="container">
        {err_html}
        <div class="card-3d">
          <h2>🔒 Kyçu si Admin</h2>
          <form method="POST">
            <label>Email</label>
            <input type="email" name="email" value="{acc['email'] if acc else ''}" required>
            <label>Fjalëkalimi</label>
            <input type="password" name="password" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">Hyr në Panel</button>
          </form>
        </div>
      </div>
    """
    return render_iphone_shell("Admin Login — Grande Studio", inner)


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel():
    init_db()
    ig_nav_html = get_ig_nav_button_html()
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
            <form method="POST" style="display:inline;">
              <input type="hidden" name="action" value="set_status">
              <input type="hidden" name="appt_id" value="{a["id"]}">
              <input type="hidden" name="status" value="Konfirmuar">
              <button class="btn-sm-3d btn-seen" type="submit">Prano</button>
            </form>
            <form method="POST" style="display:inline;">
              <input type="hidden" name="action" value="set_status">
              <input type="hidden" name="appt_id" value="{a["id"]}">
              <input type="hidden" name="status" value="Anuluar">
              <button class="btn-sm-3d btn-del" type="submit">Anulo</button>
            </form>
          </td>
        </tr>
    """ for a in appts)

    inner = f"""
      <header>
        <div class="header-inner">
          <div><h1 class="brand-title">GRANDE STUDIO</h1><p class="brand-sub">Admin Panel</p></div>
          <div class="header-mid">{ig_nav_html}</div>
          <div class="header-actions"><a href="{url_for('index')}" class="btn-3d btn-ghost-3d">← Klienti</a></div>
        </div>
      </header>
      <div class="container">
        <div id="pushPromptBox" class="push-banner-card">
          <div style="font-size:0.83rem; color:#45030f;">
            <b>🔔 Njoftimet e Adminit</b><br>Aktivizo njoftimet që t'ju bjerë zilja kur ka rezervim të ri.
          </div>
          <button type="button" onclick="enablePushNotifications()" class="btn-3d btn-vishnje-3d" style="padding:8px 11px; font-size:0.8rem;">Aktivizo</button>
        </div>

        <div class="card-3d">
          <h2>Terminet</h2>
          <table>
            <thead><tr><th>Data</th><th>Klienti</th><th>Shërbimi</th><th>Statusi</th><th>Veprime</th></tr></thead>
            <tbody>{rows_html if rows_html else '<tr><td colspan="5">Nuk ka termine.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    """
    return render_iphone_shell("Admin — Grande Studio", inner)


if __name__ == "__main__":
    init_db()
    prepare_clean_nails_asset()
    prepare_ig_avatar_asset()
    prepare_app_icons()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)