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
app.config["PERMANENT_SESSION_LIFETIME"] = 315360000  # 10 vjet

ADMIN_REMEMBER_COOKIE = "grande_admin_remember"
CLIENT_REMEMBER_COOKIE = "grande_client_remember"
COOKIE_MAX_AGE = 315360000  # 10 vjet

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


def get_vapid_keys() -> dict:
    if os.path.exists(VAPID_KEYS_PATH):
        try:
            with open(VAPID_KEYS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    if HAS_WEBPUSH:
        try:
            import base64
            from cryptography.hazmat.primitives import serialization
            vapid = Vapid()
            vapid.generate_keys()
            priv_pem = vapid.private_pem().decode("utf-8")
            pub_raw = vapid.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint
            )
            pub_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode("utf-8")
            keys = {"private_key": priv_pem, "public_key": pub_b64}
            with open(VAPID_KEYS_PATH, "w", encoding="utf-8") as f:
                json.dump(keys, f)
            return keys
        except Exception as e:
            print(f"[VAPID] Gabim gjatë gjenerimit të çelësave: {e}")

    return {"private_key": "", "public_key": ""}


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

    candidates = [
        os.path.join(BASE_DIR, "896023468_18110131619343947_7234878166828640368_n.jpg"),
        os.path.join(BASE_DIR, "ig_profile.jpg"),
        os.path.join(BASE_DIR, "ig_profile.png"),
        os.path.join(BASE_DIR, "instagram.jpg"),
        os.path.join(BASE_DIR, "instagram.png"),
    ]
    src_file = next((p for p in candidates if os.path.exists(p)), None)
    clean_path = os.path.join(STATIC_DIR, "ig_avatar_clean.png")

    if src_file and HAS_PIL:
        try:
            img = Image.open(src_file).convert("RGBA")
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            square = img.crop((left, top, left + side, top + side)).resize((256, 256), Image.Resampling.LANCZOS)
            square.save(clean_path, "PNG")
            return "/static/ig_avatar_clean.png"
        except Exception:
            pass

    return None


def prepare_app_icons() -> None:
    icon_192 = os.path.join(STATIC_DIR, "icon-192.png")
    icon_512 = os.path.join(STATIC_DIR, "icon-512.png")
    icon_512_jpg = os.path.join(STATIC_DIR, "icon-512.jpg")
    ig_clean_4 = os.path.join(STATIC_DIR, "ig_avatar_clean_4.png")
    ig_clean_3 = os.path.join(STATIC_DIR, "ig_avatar_clean_3.png")
    ig_clean_2 = os.path.join(STATIC_DIR, "ig_avatar_clean_2.png")
    ig_clean_1 = os.path.join(STATIC_DIR, "ig_avatar_clean.png")

    logo_sources = [
        icon_512_jpg,
        ig_clean_4,
        ig_clean_3,
        ig_clean_2,
        icon_192,
        ig_clean_1,
        os.path.join(BASE_DIR, "896023468_18110131619343947_7234878166828640368_n.jpg"),
        os.path.join(BASE_DIR, "ig_profile.jpg"),
        os.path.join(BASE_DIR, "ig_profile.png"),
        os.path.join(STATIC_DIR, "ig_profile.jpg"),
        os.path.join(STATIC_DIR, "ig_profile.png"),
    ]
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

        img_192 = square.resize((192, 192), Image.Resampling.LANCZOS)
        img_512 = square.resize((512, 512), Image.Resampling.LANCZOS)

        if not os.path.exists(icon_192) or os.path.getmtime(icon_192) < os.path.getmtime(chosen_logo):
            img_192.save(icon_192, "PNG", optimize=True)

        if not os.path.exists(icon_512) or os.path.getmtime(icon_512) < os.path.getmtime(chosen_logo):
            img_512.save(icon_512, "PNG", optimize=True)
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

    cur.execute("PRAGMA table_info(client_accounts)")
    c_cols = [row["name"] for row in cur.fetchall()]
    if "auth_method" not in c_cols:
        cur.execute("ALTER TABLE client_accounts ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'instagram'")
    if "is_blocked" not in c_cols:
        cur.execute("ALTER TABLE client_accounts ADD COLUMN is_blocked INTEGER DEFAULT 0")
    if "password_reset_required" not in c_cols:
        cur.execute("ALTER TABLE client_accounts ADD COLUMN password_reset_required INTEGER DEFAULT 0")

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

    cur.execute("PRAGMA table_info(appointments)")
    cols = [row["name"] for row in cur.fetchall()]
    if "is_new" not in cols:
        cur.execute("ALTER TABLE appointments ADD COLUMN is_new INTEGER DEFAULT 0")
    if "client_id" not in cols:
        cur.execute("ALTER TABLE appointments ADD COLUMN client_id INTEGER")
    if "client_instagram" not in cols:
        cur.execute("ALTER TABLE appointments ADD COLUMN client_instagram TEXT DEFAULT ''")
    if "client_email" not in cols:
        cur.execute("ALTER TABLE appointments ADD COLUMN client_email TEXT DEFAULT ''")

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

    cur.execute("SELECT COUNT(*) AS cnt FROM services")
    if cur.fetchone()["cnt"] == 0:
        default_services = [
            ("Thonj", "Manikyr me Xhel (Gel Polish)", 60, 15.0, "Pastrim kutikulash, formësim dhe xhel me shkëlqim vishnje."),
            ("Thonj", "Zgjatje me Tipsa / Xhel", 90, 25.0, "Zgjatje natyrale ose të gjata me përforcim xheli."),
            ("Thonj", "Pedikyr Estetik + Xhel", 75, 22.0, "Trajtim i plotë i shputave dhe ngjyrë xheli."),
            ("Qerpikë", "Lash Lift & Tint (Ngritje Qerpikësh)", 60, 25.0, "Ngritje dhe ngjyrosje për pamje të hapur e natyrale."),
            ("Qerpikë", "Zgjatje Qerpikësh Classic / Volume", 120, 35.0, "Fije të lehta dhe të buta të përshtatura për formën e syrit."),
            ("Vetulla", "Laminim Vetullash + Formësim", 45, 20.0, "Rregullim, ushqim dhe formësim i saktë i vetullave."),
            ("Vetulla", "Dizajn, Depilim & Ngjyrosje Vetullash", 30, 12.0, "Pastrim dhe nuancim harmonik sipas fizionomisë."),
            ("Kombinim", "Paketa Grande: Manikyr Xhel + Laminim Vetullash", 105, 32.0, "Kombinim ideal për thonj të kuruar dhe vetulla të rregulluara."),
            ("Kombinim", "Kombo Qerpikë (Lash Lift) + Vetulla (Dizajn & Tint)", 90, 33.0, "Pamje e freskët e syve pa nevojë për grim të përditshëm."),
            ("Kombinim", "Total Glow: Thonj Xhel + Qerpikë + Vetulla", 150, 52.0, "Trajtim i plotë i kombinuar për thonj, qerpikë dhe vetulla."),
        ]
        cur.executemany(
            "INSERT INTO services (category, name, duration_min, price, description) VALUES (?, ?, ?, ?, ?)",
            default_services,
        )

    conn.commit()
    conn.close()


def create_admin_remember_token() -> str:
    token = secrets.token_urlsafe(48)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO admin_sessions (token, created_at) VALUES (?, ?)",
        (token, datetime.now().isoformat()),
    )
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
    cur = conn.cursor()
    cur.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def create_client_remember_token(client_id: int) -> str:
    token = secrets.token_urlsafe(48)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO client_sessions (token, client_id, created_at) VALUES (?, ?, ?)",
        (token, client_id, datetime.now().isoformat()),
    )
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
    cur = conn.cursor()
    cur.execute("DELETE FROM client_sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def _deliver_push_payload(rows, table_name: str, title: str, body: str, url: str = "/", play_sound: bool = True) -> None:
    if not HAS_WEBPUSH or not rows:
        return
    vapid = get_vapid_keys()
    if not vapid.get("private_key"):
        return

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "playSound": play_sound,
        "timestamp": datetime.now().isoformat()
    })

    dead_ids = []
    for sub in rows:
        try:
            sub_info = json.loads(sub["subscription_json"])
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=vapid["private_key"],
                vapid_claims={"sub": "mailto:admin@grandestudio.local"},
                ttl=86400,
                headers={
                    "Urgency": "high",
                    "Topic": "grande-studio-alert"
                }
            )
        except WebPushException as ex:
            if ex.response and ex.response.status_code in (404, 410):
                dead_ids.append(sub["id"])
        except Exception:
            pass

    if dead_ids:
        conn = get_db()
        cur = conn.cursor()
        cur.executemany(f"DELETE FROM {table_name} WHERE id = ?", [(i,) for i in dead_ids])
        conn.commit()
        conn.close()


def send_push_to_admins(title: str, body: str, url: str = "/admin?play_sound=1") -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO admin_notifications (title, body, is_read, created_at)
        VALUES (?, ?, 0, ?)
        """,
        (title, body, datetime.now().isoformat())
    )
    cur.execute("SELECT id, subscription_json FROM push_subscriptions")
    subs = cur.fetchall()
    conn.commit()
    conn.close()
    _deliver_push_payload(subs, "push_subscriptions", title, body, url=url, play_sound=True)


def notify_client_message(client_id: int | None, title: str, body: str, event_type: str = "info") -> None:
    if not client_id:
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO client_notifications (client_id, title, body, event_type, is_read, created_at)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (client_id, title, body, event_type, datetime.now().isoformat()),
    )
    cur.execute("SELECT id, subscription_json FROM client_push_subscriptions WHERE client_id = ?", (client_id,))
    subs = cur.fetchall()
    conn.commit()
    conn.close()

    _deliver_push_payload(subs, "client_push_subscriptions", title, body, url="/?play_sound=1", play_sound=True)


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


def generate_temp_password(length: int = 12) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def send_reset_email(to_email: str, new_password: str) -> tuple[bool, str]:
    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        try:
            msg = EmailMessage()
            msg["Subject"] = "Grande Studio — Reset Fjalëkalimi Admin"
            msg["From"] = SMTP_FROM
            msg["To"] = to_email
            msg.set_content(
                f"Përshëndetje,\n\n"
                f"Është kryer resetimi i fjalëkalimit për panelin e Adminit te Grande Studio.\n"
                f"Fjalëkalimi juaj i ri është: {new_password}\n\n"
                f"Pas kyçjes, rekomandohet ta ndryshoni nga seksioni i fjalëkalimit në panelin Admin.\n"
            )
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            return True, f"Fjalëkalimi i ri u dërgua në emailin {to_email}."
        except Exception as exc:
            return False, f"Gabim gjatë dërgimit të email-it: {exc}"

    print(f"\n[GRANDE STUDIO - EMAIL RESET] Për: {to_email} | Fjalëkalimi i ri: {new_password}\n")
    return True, f"Fjalëkalimi i ri u gjenerua dhe u dërgua te {to_email} (verifikoni emailin ose terminalin lokal)."


def send_client_reset_email(to_email: str) -> bool:
    if not to_email or "@" not in to_email:
        return False
    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        try:
            msg = EmailMessage()
            msg["Subject"] = "Grande Studio — Kërkesë për Reset Fjalëkalimi"
            msg["From"] = SMTP_FROM
            msg["To"] = to_email
            msg.set_content(
                "Përshëndetje,\n\n"
                "Administratori i Grande Studio ka kërkuar vendosjen e një fjalëkalimi të ri për llogarinë tuaj.\n"
                "Hapni aplikacionin e Grande Studio dhe do t'ju shfaqet direkt dritarja për vendosjen e fjalëkalimit të ri.\n"
            )
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            return True
        except Exception:
            return False
    print(f"\n[GRANDE STUDIO - CLIENT RESET EMAIL] U dërgua njoftimi te emaili: {to_email}\n")
    return True


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
        """
        SELECT start_time, end_time FROM appointments
        WHERE appt_date = ? AND status IN ('Konfirmuar', 'Bllokuar')
        """,
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

  * {
    box-sizing: border-box;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif;
  }

  body {
    margin: 0;
    min-height: 100vh;
    font-size: 16px;
    color: var(--text);
    background-color: #26030a;
    background-image:
      radial-gradient(circle at 18% 20%, rgba(184, 29, 61, 0.42), transparent 45%),
      radial-gradient(circle at 82% 80%, rgba(150, 17, 43, 0.48), transparent 50%),
      url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='320' viewBox='0 0 320 320'%3E%3Cg fill='none' stroke='rgba(255,215,225,0.09)' stroke-width='1.5'%3E%3Cpath d='M35 85 C75 60, 125 60, 165 85 C125 105, 75 105, 35 85 Z'/%3E%3Cpath d='M45 73 L33 58 M72 65 L66 47 M100 63 L100 44 M128 65 L134 47 M155 73 L167 58'/%3E%3Cpath d='M30 46 C72 24, 128 24, 170 46' stroke-width='2.4'/%3E%3Crect x='215' y='165' width='18' height='75' rx='9'/%3E%3Crect x='240' y='150' width='18' height='90' rx='9'/%3E%3Crect x='265' y='160' width='18' height='80' rx='9'/%3E%3Cpath d='M217 166 C217 153, 231 153, 231 166 Z' fill='rgba(255,190,206,0.12)'/%3E%3Cpath d='M242 151 C242 137, 256 137, 256 151 Z' fill='rgba(255,190,206,0.14)'/%3E%3Cpath d='M267 161 C267 148, 281 148, 281 161 Z' fill='rgba(255,190,206,0.12)'/%3E%3C/g%3E%3C/svg%3E");
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 18px 12px;
  }

  .studio-stage-label {
    color: #ffdce4;
    font-size: 0.9rem;
    letter-spacing: 0.5px;
    margin-bottom: 10px;
    text-align: center;
    opacity: 0.9;
  }

  .iphone-chassis {
    width: 412px;
    max-width: 100%;
    height: 852px;
    max-height: calc(100vh - 64px);
    background: #141112;
    border-radius: 52px;
    padding: 13px;
    box-shadow:
      0 0 0 2px #5e2430,
      0 0 0 5px #24090f,
      0 28px 65px rgba(0, 0, 0, 0.65);
    position: relative;
    display: flex;
    flex-direction: column;
  }

  .iphone-chassis::before {
    content: "";
    position: absolute;
    left: -4px;
    top: 150px;
    width: 3px;
    height: 46px;
    background: #6b2937;
    border-radius: 3px 0 0 3px;
    box-shadow: 0 62px 0 #6b2937;
  }

  .iphone-chassis::after {
    content: "";
    position: absolute;
    right: -4px;
    top: 185px;
    width: 3px;
    height: 74px;
    background: #6b2937;
    border-radius: 0 3px 3px 0;
  }

  .iphone-screen {
    flex: 1;
    border-radius: 40px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    position: relative;
    background-color: #fdf5f7;
    background-image:
      radial-gradient(circle at top right, rgba(184, 29, 61, 0.11), transparent 48%),
      radial-gradient(circle at bottom left, rgba(114, 9, 29, 0.09), transparent 52%),
      url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='260' height='260' viewBox='0 0 260 260'%3E%3Cg fill='none' stroke='rgba(114,9,29,0.055)' stroke-width='1.4'%3E%3Cpath d='M25 65 C55 45, 95 45, 125 65 C95 82, 55 82, 25 65 Z'/%3E%3Cpath d='M35 55 L26 43 M56 49 L51 35 M75 47 L75 32 M94 49 L99 35 M115 55 L124 43'/%3E%3Cpath d='M22 34 C55 16, 95 16, 128 34' stroke-width='2'/%3E%3Crect x='172' y='145' width='14' height='58' rx='7'/%3E%3Crect x='192' y='133' width='14' height='70' rx='7'/%3E%3Crect x='212' y='142' width='14' height='61' rx='7'/%3E%3Cpath d='M174 146 C174 136, 184 136, 184 146 Z' fill='rgba(184,29,61,0.07)'/%3E%3Cpath d='M194 134 C194 123, 204 123, 204 134 Z' fill='rgba(184,29,61,0.09)'/%3E%3Cpath d='M214 143 C214 133, 224 133, 224 143 Z' fill='rgba(184,29,61,0.07)'/%3E%3C/g%3E%3C/svg%3E");
  }

  .ios-status-bar {
    background: linear-gradient(180deg, #4f0413, #630719);
    color: #fff;
    padding: 10px 22px 6px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.82rem;
    font-weight: 700;
    flex-shrink: 0;
  }

  .dynamic-island {
    width: 96px;
    height: 24px;
    background: #090708;
    border-radius: 999px;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding-right: 9px;
  }

  .dynamic-island-cam {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: radial-gradient(circle, #283b63 25%, #0d1322 70%);
  }

  .app-scroll-area {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    -webkit-overflow-scrolling: touch;
  }

  .ios-home-indicator {
    height: 20px;
    background: rgba(253, 245, 247, 0.92);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .ios-home-indicator span {
    width: 124px;
    height: 4px;
    background: #3b1922;
    border-radius: 999px;
    opacity: 0.45;
  }

  @media (max-width: 520px), (display-mode: standalone) {
    body { padding: 0; background: var(--bg); display: block; }
    .studio-stage-label, .ios-status-bar, .ios-home-indicator { display: none !important; }
    .iphone-chassis { width: 100%; height: auto; min-height: 100vh; max-height: none; border-radius: 0; padding: 0; background: transparent; box-shadow: none; }
    .iphone-chassis::before, .iphone-chassis::after { display: none; }
    .iphone-screen { border-radius: 0; overflow: visible; min-height: 100vh; }
    .app-scroll-area { overflow: visible; }
    header { padding-top: calc(12px + env(safe-area-inset-top)); }
    .container { padding-bottom: calc(28px + env(safe-area-inset-bottom)); }
  }

  header {
    background: linear-gradient(145deg, var(--vishnje-bright), var(--vishnje) 55%, var(--vishnje-dark));
    color: #fff;
    padding: 12px 12px;
    position: sticky;
    top: 0;
    z-index: 30;
    box-shadow: 0 5px 0 var(--vishnje-dark), 0 10px 20px rgba(74, 4, 17, 0.25);
    border-bottom: 1px solid rgba(255, 255, 255, 0.18);
  }

  .header-inner {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
  }

  .header-mid {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 5px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .brand-title {
    margin: 0;
    font-size: 1.04rem;
    letter-spacing: 0.4px;
    font-weight: 800;
    white-space: nowrap;
    text-shadow: 0 2px 4px rgba(0, 0, 0, 0.35);
  }

  .brand-sub {
    margin: 2px 0 0;
    font-size: 0.74rem;
    color: #ffd6df;
    white-space: nowrap;
  }

  .btn-3d {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    border: none;
    border-radius: 11px;
    font-weight: 700;
    cursor: pointer;
    text-decoration: none;
    transition: transform 0.12s ease, box-shadow 0.12s ease, filter 0.15s ease;
    user-select: none;
  }

  .btn-3d:hover { filter: brightness(1.04); }
  .btn-3d:active { transform: translateY(3px); }

  .btn-vishnje-3d {
    background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje));
    color: #fff;
    padding: 11px 15px;
    font-size: 0.96rem;
    box-shadow: 0 4px 0 var(--vishnje-dark), 0 7px 14px rgba(114, 9, 29, 0.26);
  }

  .btn-vishnje-3d:active {
    box-shadow: 0 2px 0 var(--vishnje-dark);
  }

  .btn-ghost-3d {
    background: rgba(255, 255, 255, 0.15);
    color: #fff;
    padding: 6px 8px;
    font-size: 0.78rem;
    border: 1px solid rgba(255, 255, 255, 0.28);
    box-shadow: 0 3px 0 rgba(45, 2, 10, 0.65);
    white-space: nowrap;
  }

  .btn-ghost-3d:active {
    box-shadow: 0 1px 0 rgba(45, 2, 10, 0.65);
  }

  .btn-ig-3d {
    background: linear-gradient(135deg, #f58529, #dd2a7b 55%, #8134af);
    color: #fff;
    padding: 6px 9px;
    font-size: 0.8rem;
    border: 1px solid rgba(255, 255, 255, 0.42);
    box-shadow: 0 3px 0 rgba(45, 2, 10, 0.7);
    white-space: nowrap;
  }

  .btn-ig-3d:active {
    box-shadow: 0 1px 0 rgba(45, 2, 10, 0.7);
  }

  .container {
    padding: 13px 12px 28px;
  }

  .combo-banner {
    background: linear-gradient(135deg, #47020e 0%, #7a0a1f 55%, #a81632 100%);
    color: #fff;
    border-radius: 17px;
    padding: 13px 13px 16px;
    margin-bottom: 13px;
    position: relative;
    overflow: hidden;
    min-height: 184px;
    box-shadow: 0 5px 0 var(--vishnje-dark), 0 12px 22px rgba(114, 9, 29, 0.22);
  }

  .ig-banner-avatar {
    position: absolute;
    right: 26px;
    top: 8px;
    width: 72px;
    height: 72px;
    border-radius: 50%;
    object-fit: cover;
    border: 2px solid rgba(255, 255, 255, 0.88);
    box-shadow: 0 4px 12px rgba(15, 0, 4, 0.82);
    pointer-events: none;
    z-index: 2;
  }

  .ig-fallback-badge {
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, #f58529, #dd2a7b, #8134af);
    color: #fff;
    font-weight: 900;
    font-size: 1.05rem;
  }

  .nails-banner-bottom {
    position: absolute;
    right: 2px;
    bottom: 18px;
    width: 158px;
    max-width: 44%;
    height: auto;
    pointer-events: none;
    opacity: 1;
    filter: drop-shadow(0 2px 6px rgba(18, 0, 4, 0.9));
    z-index: 1;
  }

  .combo-banner-content {
    position: relative;
    z-index: 3;
    max-width: 100%;
  }

  .combo-banner h3 {
    margin: 0 0 4px;
    font-size: 1.04rem;
    font-weight: 800;
    color: #ffffff;
    text-shadow: 0 2px 6px rgba(25, 1, 5, 0.92);
  }

  .combo-banner p {
    margin: 0 0 10px;
    font-size: 0.84rem;
    color: #ffffff;
    line-height: 1.38;
    max-width: 56%;
    text-shadow: 0 1px 5px rgba(25, 1, 5, 0.92);
  }

  .pillar-pills {
    position: relative;
    z-index: 4;
    display: grid;
    grid-template-columns: repeat(2, max-content);
    gap: 5px 6px;
    width: fit-content;
  }

  .pillar-pill {
    position: relative;
    z-index: 4;
    background: rgba(42, 2, 10, 0.86);
    border: 1px solid rgba(255, 255, 255, 0.55);
    padding: 4px 9px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 700;
    color: #ffffff;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.42);
    white-space: nowrap;
  }

  .tabs-bar {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
    margin-bottom: 13px;
  }

  .tab-btn {
    background: linear-gradient(180deg, #ffffff, #f8e8ec);
    color: var(--vishnje);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 9px 8px;
    font-size: 0.86rem;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 4px 0 #dec0c8;
    transition: transform 0.12s ease, box-shadow 0.12s ease;
    text-align: center;
  }

  .tab-btn:active { transform: translateY(3px); box-shadow: 0 1px 0 #dec0c8; }
  .tab-btn.active {
    background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje));
    color: #fff;
    border-color: var(--vishnje-dark);
    box-shadow: 0 4px 0 var(--vishnje-dark);
  }

  .tab-panel { display: none; }
  .tab-panel.active { display: block; animation: fadeIn 0.2s ease; }

  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .card-3d {
    background: rgba(255, 255, 255, 0.96);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 14px;
    margin-bottom: 13px;
    box-shadow: 0 5px 0 #e6cbd2, 0 10px 20px rgba(74, 4, 17, 0.07);
  }

  .auth-gate-card {
    border: 2px solid var(--vishnje-bright);
    background: linear-gradient(180deg, #ffffff, #fff5f7);
  }

  .auth-mode-switch {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 7px;
    margin-bottom: 10px;
  }

  .auth-mode-btn {
    padding: 8px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: #fff;
    color: var(--vishnje);
    font-size: 0.86rem;
    font-weight: 800;
    cursor: pointer;
  }

  .auth-mode-btn.active {
    background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje));
    color: #fff;
    border-color: var(--vishnje-dark);
  }

  .method-choice-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 7px;
    margin-top: 4px;
    margin-bottom: 6px;
  }

  .method-chip {
    padding: 9px 8px;
    border-radius: 10px;
    border: 1.5px solid #e2c2ca;
    background: #fff;
    color: var(--vishnje-dark);
    font-size: 0.85rem;
    font-weight: 800;
    cursor: pointer;
    text-align: center;
  }

  .method-chip.active {
    background: var(--vishnje-soft);
    border-color: var(--vishnje-bright);
    color: var(--vishnje);
    box-shadow: 0 2px 0 #e6cbd2;
  }

  .ig-autofill-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin-top: 5px;
    font-size: 0.78rem;
    color: var(--muted);
  }

  .ig-mini-btn {
    background: var(--vishnje-soft);
    color: var(--vishnje);
    border: 1px solid #dfb4bf;
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 0.76rem;
    font-weight: 700;
    cursor: pointer;
    white-space: nowrap;
  }

  .client-session-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    background: #fff3f6;
    border: 1px solid #f2c4cf;
    border-radius: 12px;
    padding: 9px 11px;
    margin-bottom: 10px;
    font-size: 0.84rem;
    color: var(--vishnje-dark);
  }

  .client-notif-box {
    border-radius: 14px;
    padding: 11px 12px;
    margin-bottom: 11px;
    font-size: 0.88rem;
    font-weight: 700;
    line-height: 1.45;
    transition: transform 0.25s ease, opacity 0.25s ease;
    touch-action: pan-x;
    user-select: none;
  }

  .client-notif-accepted {
    background: #ebfaef;
    color: #166534;
    border: 1.5px solid #86efac;
  }

  .client-notif-rejected {
    background: #fff0f2;
    color: #9f1239;
    border: 1.5px solid #fda4af;
  }

  .remember-me-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 10px;
    font-size: 0.86rem;
    color: var(--vishnje-dark);
    font-weight: 700;
  }

  .remember-me-row input[type="checkbox"] {
    width: 18px;
    height: 18px;
    accent-color: var(--vishnje-bright);
    cursor: pointer;
  }

  h2 {
    margin: 0 0 9px;
    font-size: 1.08rem;
    color: var(--vishnje);
  }

  .cat-filter-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .cat-chip-3d {
    background: linear-gradient(180deg, #fff, #f9eaee);
    color: var(--vishnje);
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 6px 10px;
    font-size: 0.82rem;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 3px 0 #dfc2ca;
  }

  .cat-chip-3d:active { transform: translateY(2px); box-shadow: 0 1px 0 #dfc2ca; }
  .cat-chip-3d.active {
    background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje));
    color: #fff;
    border-color: var(--vishnje-dark);
    box-shadow: 0 3px 0 var(--vishnje-dark);
  }

  label {
    display: block;
    font-size: 0.88rem;
    font-weight: 700;
    margin: 10px 0 4px;
    color: var(--vishnje-dark);
  }

  input, select, textarea {
    width: 100%;
    padding: 10px 11px;
    border: 1.5px solid #e2c2ca;
    border-radius: 10px;
    font-size: 0.95rem;
    background: #fffdfd;
    color: var(--text);
  }

  input:focus, select:focus, textarea:focus {
    outline: none;
    border-color: var(--vishnje-bright);
    box-shadow: 0 0 0 3px rgba(184, 29, 61, 0.14);
  }

  .slots { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .slot-3d {
    padding: 8px 11px;
    border-radius: 9px;
    border: 1px solid #dfb9c3;
    background: linear-gradient(180deg, #ffffff, #fdf0f3);
    color: var(--vishnje);
    font-size: 0.88rem;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 3px 0 #d6aeb8;
  }

  .slot-3d:active { transform: translateY(2px); box-shadow: 0 1px 0 #d6aeb8; }
  .slot-3d.active {
    background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje));
    color: #fff;
    border-color: var(--vishnje-dark);
    box-shadow: 0 3px 0 var(--vishnje-dark);
  }

  .svc-grid { display: grid; grid-template-columns: 1fr; gap: 10px; margin-top: 9px; }
  .svc-card-3d {
    background: linear-gradient(180deg, #ffffff, #fff7f9);
    border: 1px solid var(--border);
    border-radius: 13px;
    padding: 12px;
    box-shadow: 0 4px 0 #e5c8d0;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 7px;
  }

  .svc-tag {
    display: inline-block;
    background: var(--vishnje-soft);
    color: var(--vishnje);
    border: 1px solid #e6c2cb;
    font-size: 0.76rem;
    font-weight: 800;
    padding: 2px 8px;
    border-radius: 999px;
    margin-bottom: 4px;
  }

  .svc-title { font-weight: 800; font-size: 0.96rem; color: var(--text); }
  .svc-desc { font-size: 0.86rem; color: var(--muted); line-height: 1.4; margin: 3px 0; }
  .svc-meta { font-size: 0.88rem; font-weight: 700; color: var(--vishnje); }

  .alert {
    padding: 10px 12px;
    border-radius: 12px;
    margin-bottom: 11px;
    font-size: 0.9rem;
    font-weight: 600;
  }

  .alert-ok { background: #ebfaef; color: var(--ok); border: 1px solid #a8e4ba; }
  .alert-err { background: #fff0f2; color: var(--warn); border: 1px solid #f7bcc4; }

  @keyframes pulseNew {
    0% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0.55); }
    70% { box-shadow: 0 0 0 8px rgba(225, 29, 72, 0); }
    100% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0); }
  }

  .new-booking-card {
    background: linear-gradient(135deg, #fff1f2 0%, #ffe4e6 100%);
    border: 2px solid #e11d48;
    border-radius: 14px;
    padding: 12px;
    margin-bottom: 10px;
    animation: pulseNew 2s infinite;
  }

  .badge-new-live {
    background: #e11d48;
    color: #fff;
    font-size: 0.76rem;
    font-weight: 900;
    padding: 3px 8px;
    border-radius: 999px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    display: inline-block;
  }

  .live-toast-banner {
    position: fixed;
    top: 14px;
    left: 50%;
    transform: translateX(-50%);
    width: 92%;
    max-width: 390px;
    background: linear-gradient(135deg, #9f1239, #e11d48);
    color: #fff;
    padding: 14px;
    border-radius: 16px;
    z-index: 9999;
    box-shadow: 0 15px 35px rgba(159, 18, 57, 0.55);
    border: 2px solid #fda4af;
    display: none;
  }

  .install-prompt-box {
    background: linear-gradient(135deg, #fff5f7, #ffe4ea);
    border: 1.5px solid #f43f5e;
    border-radius: 14px;
    padding: 10px 12px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
  th, td { padding: 7px 5px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
  th { color: var(--vishnje); font-size: 0.78rem; text-transform: uppercase; }

  .row-is-new { background-color: #fff1f2 !important; }

  .badge { padding: 2px 7px; border-radius: 999px; font-size: 0.76rem; font-weight: 800; }
  .b-ok { background: #d3f9d8; color: #1f7a3d; }
  .b-blk { background: #ffe3e3; color: #b41e2d; }
  .b-can { background: #f1f3f5; color: #495057; }

  .btn-sm-3d {
    padding: 6px 9px;
    border-radius: 8px;
    border: none;
    cursor: pointer;
    font-size: 0.82rem;
    font-weight: 700;
    box-shadow: 0 2px 0 rgba(0,0,0,0.18);
    margin-bottom: 3px;
  }

  .btn-sm-3d:active { transform: translateY(2px); box-shadow: none; }
  .btn-del { background: #ffe3e3; color: #b41e2d; }
  .btn-seen { background: #dcfce7; color: #166534; }
  .btn-reset { background: #fef3c7; color: #92400e; }

  .client-item-card {
    border: 1px solid var(--border);
    background: #fff9fb;
    border-radius: 12px;
    padding: 10px;
    margin-bottom: 9px;
    box-shadow: 0 3px 0 #ead0d7;
  }

  .edit-svc-box {
    border: 1px solid var(--border);
    background: #fff9fb;
    border-radius: 12px;
    padding: 10px;
    margin-bottom: 10px;
    box-shadow: 0 3px 0 #ead0d7;
  }

  .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  @media (max-width: 420px) { .row-2 { grid-template-columns: 1fr; } }
</style>
"""


def render_iphone_shell(title: str, inner_html: str, description: str = "Grande Studio — Rezervime online për Thonj, Qerpikë dhe Vetulla. Zgjidhni trajtimin dhe orarin tuaj me një klikim.") -> str:
    now_clock = datetime.now().strftime("%H:%M")
    base_url = request.url_root.rstrip("/") if request else ""
    canonical_url = f"{base_url}/" if base_url else "/"
    v = get_asset_version()
    icon_url = f"{base_url}/static/icon-512.png?v={v}" if base_url else f"/static/icon-512.png?v={v}"

    schema_json = json.dumps({
        "@context": "https://schema.org",
        "@type": "BeautySalon",
        "name": "Grande Studio",
        "description": description,
        "url": canonical_url,
        "image": icon_url,
        "sameAs": [IG_WEB_URL],
        "openingHours": f"Mo-Sa {WORK_START}-{WORK_END}",
        "priceRange": "$$"
    }, ensure_ascii=False)

    return f"""
    <!doctype html>
    <html lang="sq" translate="no">
    <head>
      <meta charset="utf-8">
      <meta name="google" content="notranslate">
      <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
      <title>{title}</title>
      <meta name="description" content="{description}">
      <meta name="keywords" content="Grande Studio, thonj, qerpikë, vetulla, manikyr me xhel, lash lift, laminim vetullash, rezervim online, sallon bukurie">
      <meta name="robots" content="index, follow, max-image-preview:large">
      <link rel="canonical" href="{canonical_url}">

      <meta property="og:type" content="website">
      <meta property="og:site_name" content="Grande Studio">
      <meta property="og:title" content="{title}">
      <meta property="og:description" content="{description}">
      <meta property="og:url" content="{canonical_url}">
      <meta property="og:image" content="{icon_url}">

      <meta name="theme-color" content="#72091d">
      <meta name="mobile-web-app-capable" content="yes">
      <meta name="apple-mobile-web-app-capable" content="yes">
      <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
      <meta name="apple-mobile-web-app-title" content="Grande Studio">
      <link rel="apple-touch-icon" sizes="192x192" href="/static/icon-192.png?v={v}">
      <link rel="icon" type="image/png" sizes="192x192" href="/static/icon-192.png?v={v}">
      <link rel="manifest" href="/manifest.json?v={v}">

      <script type="application/ld+json">{schema_json}</script>
      {BASE_STYLE}
    </head>
    <body class="notranslate">
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
        if ('serviceWorker' in navigator) {{
          window.addEventListener('load', () => {{
            navigator.serviceWorker.register('/sw.js?v={v}', {{ scope: '/' }}).then(reg => {{
              if (reg && reg.update) reg.update();
            }}).catch(() => {{}});
          }});
        }}

        function playNotificationChime() {{
          try {{
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            const ctx = new AudioCtx();
            if (ctx.state === 'suspended') {{
              ctx.resume();
            }}
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

        window.addEventListener('load', () => {{
          const params = new URLSearchParams(window.location.search);
          if (params.get('play_sound') === '1') {{
            playNotificationChime();
          }}
        }});

        function urlBase64ToUint8Array(base64String) {{
          const padding = '='.repeat((4 - base64String.length % 4) % 4);
          const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
          const rawData = window.atob(base64);
          const outputArray = new Uint8Array(rawData.length);
          for (let i = 0; i < rawData.length; ++i) {{
            outputArray[i] = rawData.charCodeAt(i);
          }}
          return outputArray;
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


@app.route("/robots.txt")
def robots_txt():
    base_url = request.url_root.rstrip("/")
    txt = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        f"Sitemap: {base_url}/sitemap.xml\n"
    )
    return Response(txt, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    base_url = request.url_root.rstrip("/")
    today = datetime.now().strftime("%Y-%m-%d")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{base_url}/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""
    return Response(xml, mimetype="application/xml")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/manifest.json")
def manifest():
    prepare_ig_avatar_asset()
    prepare_app_icons()
    v = get_asset_version()
    icons = [
        {
            "src": f"/static/icon-192.png?v={v}",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any"
        },
        {
            "src": f"/static/icon-512.png?v={v}",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any"
        }
    ]

    resp = jsonify({
        "id": "/",
        "name": "Grande Studio — Thonj, Qerpikë & Vetulla",
        "short_name": "Grande Studio",
        "description": "Rezervime online për Thonj, Qerpikë dhe Vetulla te Grande Studio.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#34040d",
        "theme_color": "#72091d",
        "icons": icons,
    })
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/sw.js")
def service_worker():
    v = get_asset_version()
    js = f"""
    const CACHE_NAME = 'grande-studio-{v}';
    const CORE_ASSETS = [
      '/manifest.json?v={v}',
      '/static/icon-192.png?v={v}',
      '/static/icon-512.png?v={v}'
    ];

    self.addEventListener('install', event => {{
      event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(CORE_ASSETS).catch(() => {{}}))
      );
      self.skipWaiting();
    }});

    self.addEventListener('activate', event => {{
      event.waitUntil(
        caches.keys().then(keys =>
          Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
      );
    }});

    self.addEventListener('fetch', event => {{
      if (event.request.method !== 'GET') return;
      const url = new URL(event.request.url);
      if (url.origin !== self.location.origin) return;

      if (url.pathname === '/' || url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin')) {{
        event.respondWith(fetch(event.request, {{ cache: 'no-store' }}));
        return;
      }}

      event.respondWith(
        fetch(event.request)
          .then(resp => {{
            if (resp && resp.status === 200) {{
              const copy = resp.clone();
              caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
            }}
            return resp;
          }})
          .catch(() => caches.match(event.request))
      );
    }});

    self.addEventListener('push', event => {{
      let data = {{
        title: '🔔 Njoftim — Grande Studio',
        body: 'Keni një njoftim të ri nga Grande Studio.',
        url: '/'
      }};
      if (event.data) {{
        try {{
          data = Object.assign(data, event.data.json());
        }} catch (e) {{
          data.body = event.data.text();
        }}
      }}

      const notifTag = 'grande-studio-' + Date.now();
      const options = {{
        body: data.body,
        icon: '/static/icon-192.png?v={v}',
        badge: '/static/icon-192.png?v={v}',
        vibrate: [500, 200, 500, 200, 500],
        tag: notifTag,
        renotify: true,
        requireInteraction: true,
        silent: false,
        timestamp: Date.now(),
        data: {{ url: data.url || '/' }}
      }};

      event.waitUntil(
        self.registration.showNotification(data.title, options).then(() =>
          clients.matchAll({{ type: 'window', includeUncontrolled: true }}).then(clientList => {{
            clientList.forEach(c => c.postMessage({{ type: 'PLAY_CHIME', payload: data }}));
          }})
        )
      );
    }});

    self.addEventListener('pushsubscriptionchange', event => {{
      event.waitUntil(
        fetch('/api/push/public-key')
          .then(res => res.json())
          .then(keyData => {{
            if (!keyData.publicKey) return;
            const padding = '='.repeat((4 - keyData.publicKey.length % 4) % 4);
            const base64 = (keyData.publicKey + padding).replace(/-/g, '+').replace(/_/g, '/');
            const rawData = atob(base64);
            const appServerKey = new Uint8Array(rawData.length);
            for (let i = 0; i < rawData.length; ++i) appServerKey[i] = rawData.charCodeAt(i);
            return self.registration.pushManager.subscribe({{
              userVisibleOnly: true,
              applicationServerKey: appServerKey
            }});
          }})
          .then(sub => {{
            if (!sub) return;
            return fetch('/api/push/subscribe', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify(sub)
            }});
          }})
          .catch(() => {{}})
      );
    }});

    self.addEventListener('notificationclick', event => {{
      event.notification.close();
      const targetUrl = (event.notification.data && event.notification.data.url) || '/';
      event.waitUntil(
        clients.matchAll({{ type: 'window', includeUncontrolled: true }}).then(clientList => {{
          for (const client of clientList) {{
            if ('focus' in client) {{
              client.postMessage({{ type: 'PLAY_CHIME' }});
              return client.focus();
            }}
          }}
          if (clients.openWindow) {{
            return clients.openWindow(targetUrl);
          }}
        }})
      );
    }});
    """
    resp = Response(js, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/admin/restore-session", methods=["POST"])
def api_admin_restore_session():
    data = request.get_json(silent=True) or {}
    tok = (data.get("token") or "").strip()
    if tok and verify_admin_remember_token(tok):
        session["admin_logged_in"] = True
        session["admin_token"] = tok
        session.permanent = True
        resp = jsonify({"ok": True})
        resp.set_cookie(
            ADMIN_REMEMBER_COOKIE,
            tok,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
        )
        return resp
    return jsonify({"ok": False}), 401


@app.route("/api/client/restore-session", methods=["POST"])
def api_client_restore_session():
    data = request.get_json(silent=True) or {}
    tok = (data.get("token") or "").strip()
    c_id = verify_client_remember_token(tok)
    if tok and c_id:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, is_blocked FROM client_accounts WHERE id = ?", (c_id,))
        acc = cur.fetchone()
        conn.close()
        if acc and acc["is_blocked"] == 0:
            session["client_id"] = c_id
            session.permanent = True
            resp = jsonify({"ok": True})
            resp.set_cookie(
                CLIENT_REMEMBER_COOKIE,
                tok,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="Lax",
            )
            return resp
    return jsonify({"ok": False}), 401


@app.route("/api/push/public-key")
def api_push_public_key():
    keys = get_vapid_keys()
    return jsonify({"publicKey": keys.get("public_key", ""), "supported": HAS_WEBPUSH})


@app.route("/api/push/subscribe", methods=["POST"])
def api_push_subscribe():
    if not is_admin_authenticated():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data = request.get_json(silent=True)
    if not data or not data.get("endpoint"):
        return jsonify({"ok": False, "error": "Abonim i pavlefshëm"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO push_subscriptions (endpoint, subscription_json, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET subscription_json = excluded.subscription_json, created_at = excluded.created_at
        """,
        (data["endpoint"], json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/client/push/subscribe", methods=["POST"])
def api_client_push_subscribe():
    client_id = session.get("client_id")
    if not client_id:
        return jsonify({"ok": False, "error": "Nuk jeni e kyçur si kliente."}), 401

    data = request.get_json(silent=True)
    if not data or not data.get("endpoint"):
        return jsonify({"ok": False, "error": "Abonim i pavlefshëm"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO client_push_subscriptions (client_id, endpoint, subscription_json, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET client_id = excluded.client_id, subscription_json = excluded.subscription_json
        """,
        (client_id, data["endpoint"], json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/client/notifications")
def api_client_notifications():
    client_id = session.get("client_id")
    if not client_id:
        return jsonify({"notifications": [], "reset_required": False})

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT is_blocked, password_reset_required FROM client_accounts WHERE id = ?", (client_id,))
    acc = cur.fetchone()
    if not acc or acc["is_blocked"] == 1:
        conn.close()
        return jsonify({"notifications": [], "reset_required": False, "blocked": True})

    cur.execute(
        """
        SELECT id, title, body, event_type, is_read, created_at
        FROM client_notifications
        WHERE client_id = ? AND is_read = 0
        ORDER BY id DESC
        LIMIT 10
        """,
        (client_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({
        "notifications": rows,
        "reset_required": bool(acc["password_reset_required"] == 1),
        "blocked": False
    })


@app.route("/api/client/notifications/read", methods=["POST"])
def api_client_notifications_read():
    client_id = session.get("client_id")
    if not client_id:
        return jsonify({"ok": False}), 401

    data = request.get_json(silent=True) or {}
    notif_id = data.get("id")

    conn = get_db()
    cur = conn.cursor()
    if notif_id:
        cur.execute("DELETE FROM client_notifications WHERE client_id = ? AND id = ?", (client_id, notif_id))
    else:
        cur.execute("DELETE FROM client_notifications WHERE client_id = ?", (client_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/notifications")
@admin_required
def api_admin_notifications():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, body, is_read, created_at
        FROM admin_notifications
        WHERE is_read = 0
        ORDER BY id DESC
        LIMIT 15
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"notifications": rows})


@app.route("/api/admin/notifications/read", methods=["POST"])
@admin_required
def api_admin_notifications_read():
    data = request.get_json(silent=True) or {}
    notif_id = data.get("id")
    conn = get_db()
    cur = conn.cursor()
    if notif_id:
        cur.execute("DELETE FROM admin_notifications WHERE id = ?", (notif_id,))
    else:
        cur.execute("DELETE FROM admin_notifications")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/new-bookings")
@admin_required
def api_admin_new_bookings():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.id, a.client_name, a.client_phone, a.client_instagram, a.client_email,
               a.appt_date, a.start_time, a.end_time, a.notes, a.created_at,
               s.name AS service_name, s.category
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        WHERE a.is_new = 1 AND a.status = 'Konfirmuar'
        ORDER BY a.id DESC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"count": len(rows), "bookings": rows})


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

    msg = None
    msg_type = "ok"
    auth_mode = "register"
    reg_method = "instagram"
    active_tab = request.form.get("active_tab") or request.args.get("tab") or "book"
    today_str = datetime.now().strftime("%Y-%m-%d")

    client_remember_token_to_set = None

    if request.method == "POST":
        form_type = request.form.get("form_type", "booking")

        if form_type == "client_register":
            auth_mode = "register"
            reg_method = request.form.get("reg_method", "instagram").strip().lower()
            if reg_method not in ("instagram", "email"):
                reg_method = "instagram"

            ig_handle = normalize_ig_handle(request.form.get("reg_instagram", "")) if reg_method == "instagram" else ""
            email_in = request.form.get("reg_email", "").strip().lower() if reg_method == "email" else ""
            phone_in = request.form.get("reg_phone", "").strip()
            pwd_in = request.form.get("reg_password", "")

            if reg_method == "instagram" and (not ig_handle or len(ig_handle) < 2):
                msg = "Ju zgjodhët variantin me Instagram: shkruani llogarinë e Instagramit."
                msg_type = "err"
            elif reg_method == "email" and (not email_in or "@" not in email_in):
                msg = "Ju zgjodhët variantin me Email: shkruani një adresë të vlefshme emaili."
                msg_type = "err"
            elif not phone_in or len(re.sub(r"\D", "", phone_in)) < 6:
                msg = "Numri i telefonit është i detyrueshëm."
                msg_type = "err"
            elif len(pwd_in) < 6:
                msg = "Fjalëkalimi (password) është i detyrueshëm dhe duhet të ketë të paktën 6 karaktere."
                msg_type = "err"
            else:
                existing = None
                if reg_method == "instagram":
                    cur.execute("SELECT id, is_blocked FROM client_accounts WHERE LOWER(instagram) = LOWER(?)", (ig_handle,))
                    existing = cur.fetchone()
                else:
                    cur.execute("SELECT id, is_blocked FROM client_accounts WHERE LOWER(email) = ?", (email_in,))
                    existing = cur.fetchone()

                if existing:
                    if existing["is_blocked"] == 1:
                        msg = "Kjo llogari është e bllokuar nga administratori."
                        msg_type = "err"
                    else:
                        msg = "Kjo llogari ekziston tashmë. Ju lutem kyçuni me fjalëkalimin tuaj."
                        msg_type = "err"
                        auth_mode = "login"
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
                    ident_label = ig_handle if reg_method == "instagram" else email_in
                    msg = f"Regjistrimi u krye me sukses ({ident_label})! Tani mund të bëni rezervimin."
                    msg_type = "ok"
                    active_tab = "book"

        elif form_type == "client_login":
            auth_mode = "login"
            login_id = request.form.get("login_identifier", "").strip()
            login_pwd = request.form.get("login_password", "")

            if not login_id or not login_pwd:
                msg = "Plotësoni Instagramin ose emailin dhe fjalëkalimin për t'u kyçur."
                msg_type = "err"
            else:
                ig_cand = normalize_ig_handle(login_id)
                email_cand = login_id.lower()
                cur.execute(
                    """
                    SELECT * FROM client_accounts
                    WHERE LOWER(email) = ? OR (instagram != '' AND LOWER(instagram) = LOWER(?))
                    """,
                    (email_cand, ig_cand),
                )
                acc_row = cur.fetchone()
                if acc_row and acc_row["is_blocked"] == 1:
                    msg = "Llogaria juaj është e bllokuar nga administratori."
                    msg_type = "err"
                elif acc_row and check_password_hash(acc_row["password_hash"], login_pwd):
                    session["client_id"] = acc_row["id"]
                    session.permanent = True
                    client_remember_token_to_set = create_client_remember_token(acc_row["id"])
                    ident_label = acc_row["instagram"] or acc_row["email"]
                    msg = f"U kyçët me sukses ({ident_label})!"
                    msg_type = "ok"
                    active_tab = "book"
                else:
                    msg = "Instagrami/Emaili ose fjalëkalimi është i pasaktë."
                    msg_type = "err"

        elif form_type == "client_set_new_password":
            cur.execute("SELECT * FROM client_accounts WHERE id = ?", (session.get("client_id") or -1,))
            current_client = cur.fetchone()
            new_cpwd = request.form.get("new_client_password", "")
            conf_cpwd = request.form.get("confirm_client_password", "")

            if not current_client or current_client["is_blocked"] == 1:
                msg = "Llogaria nuk është aktive."
                msg_type = "err"
            elif len(new_cpwd) < 6:
                msg = "Fjalëkalimi i ri duhet të ketë të paktën 6 karaktere."
                msg_type = "err"
            elif new_cpwd != conf_cpwd:
                msg = "Konfirmimi i fjalëkalimit të ri nuk përputhet."
                msg_type = "err"
            else:
                cur.execute(
                    """
                    UPDATE client_accounts
                    SET password_hash = ?, password_reset_required = 0
                    WHERE id = ?
                    """,
                    (generate_password_hash(new_cpwd), current_client["id"]),
                )
                conn.commit()
                msg = "Password-i juaj i ri u ruajt me sukses! Tani mund të vazhdoni me rezervimin."
                msg_type = "ok"
                active_tab = "book"

        elif form_type == "client_logout":
            old_c_tok = request.cookies.get(CLIENT_REMEMBER_COOKIE, "")
            delete_client_remember_token(old_c_tok)
            session.pop("client_id", None)
            msg = "Jeni çkyçur. Për të bërë rezervim, regjistrohuni ose kyçuni."
            msg_type = "ok"

        else:
            cur.execute("SELECT * FROM client_accounts WHERE id = ?", (session.get("client_id") or -1,))
            current_client = cur.fetchone()

            if not current_client:
                msg = "Nuk mund të bëni rezervim pa u regjistruar fillimisht! Zgjidhni variantin me Instagram ose Email, plus telefonin dhe password-in."
                msg_type = "err"
                active_tab = "book"
            elif current_client["is_blocked"] == 1:
                session.pop("client_id", None)
                msg = "Llogaria juaj është e bllokuar dhe nuk mund të bëni rezervime."
                msg_type = "err"
                active_tab = "book"
            elif current_client["password_reset_required"] == 1:
                msg = "Ju lutem futni një password të ri para se të bëni rezervim."
                msg_type = "err"
                active_tab = "book"
            else:
                default_label = current_client["instagram"] or current_client["email"]
                client_name = (request.form.get("client_name", "").strip() or default_label)
                client_phone = current_client["phone"]
                client_ig = current_client["instagram"]
                client_email = current_client["email"]
                service_id = request.form.get("service_id", type=int)
                appt_date = request.form.get("appt_date", "").strip()
                start_time = request.form.get("start_time", "").strip()
                notes = request.form.get("notes", "").strip()

                cur.execute("SELECT * FROM services WHERE id = ?", (service_id,))
                svc = cur.fetchone()

                if not svc or not appt_date or not start_time:
                    msg = "Ju lutem zgjidhni artikullin, datën dhe një orar të lirë."
                    msg_type = "err"
                    active_tab = "book"
                else:
                    valid_slots = get_available_slots(appt_date, svc["duration_min"])
                    if start_time not in valid_slots:
                        msg = "Ky orar nuk është më i lirë ose nuk mjafton për kohëzgjatjen e shërbimit."
                        msg_type = "err"
                        active_tab = "book"
                    else:
                        end_time = min_to_time(time_to_min(start_time) + svc["duration_min"])
                        cur.execute(
                            """
                            INSERT INTO appointments
                            (client_id, client_name, client_phone, client_instagram, client_email,
                             service_id, appt_date, start_time, end_time, notes, status, is_new, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Konfirmuar', 1, ?)
                            """,
                            (
                                current_client["id"],
                                client_name,
                                client_phone,
                                client_ig,
                                client_email,
                                service_id,
                                appt_date,
                                start_time,
                                end_time,
                                notes,
                                datetime.now().isoformat(),
                            ),
                        )
                        conn.commit()

                        contact_info = client_ig if client_ig else client_email
                        send_push_to_admins(
                            title=f"🔔 Rezervim i Ri: {client_name}",
                            body=f"{svc['name']} • {appt_date} {start_time} • {contact_info} • Tel: {client_phone}",
                            url="/admin?play_sound=1"
                        )

                        msg = f"Rezervimi te Grande Studio u regjistrua për {client_name} më {appt_date} në ora {start_time}."
                        msg_type = "ok"
                        active_tab = "book"

    logged_client = None
    client_notifs = []
    
    cookie_client_tok = request.cookies.get(CLIENT_REMEMBER_COOKIE, "")
    if not session.get("client_id") and cookie_client_tok:
        recovered_c_id = verify_client_remember_token(cookie_client_tok)
        if recovered_c_id:
            session["client_id"] = recovered_c_id
            session.permanent = True

    if session.get("client_id"):
        cur.execute("SELECT * FROM client_accounts WHERE id = ?", (session["client_id"],))
        row_c = cur.fetchone()
        if row_c and row_c["is_blocked"] == 1:
            session.pop("client_id", None)
            msg = "Llogaria juaj është e bllokuar nga administratori."
            msg_type = "err"
        else:
            logged_client = row_c
            if logged_client:
                cur.execute(
                    """
                    SELECT * FROM client_notifications
                    WHERE client_id = ? AND is_read = 0
                    ORDER BY id DESC
                    LIMIT 5
                    """,
                    (logged_client["id"],),
                )
                client_notifs = cur.fetchall()
    conn.close()

    service_options = "\n".join(
        f'<option value="{s["id"]}" data-cat="{s["category"]}" data-dur="{s["duration_min"]}">[{s["category"]}] {s["name"]} — {s["duration_min"]} min ({s["price"]:.0f}€)</option>'
        for s in services
    )

    beauty_cards_html = ""
    for s in services:
        beauty_cards_html += f"""
        <div class="svc-card-3d" data-cat-card="{s['category']}">
          <div>
            <span class="svc-tag">{s['category']}</span>
            <div class="svc-title">{s['name']}</div>
            <div class="svc-desc">{s['description']}</div>
          </div>
          <div>
            <div class="svc-meta">{s['duration_min']} min • {s['price']:.2f}€</div>
            <button type="button" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:7px; padding:8px 10px; font-size:0.86rem;"
              onclick="selectServiceAndBook({s['id']})">
              Rezervo Këtë Artikull
            </button>
          </div>
        </div>
        """

    alert_html = ""
    if msg:
        cls = "alert-ok" if msg_type == "ok" else "alert-err"
        alert_html = f'<div class="alert {cls}">{msg}</div>'

    client_notifs_html = ""
    if client_notifs:
        items_html = ""
        for n in client_notifs:
            box_cls = "client-notif-accepted" if n["event_type"] == "accepted" else "client-notif-rejected"
            icon_txt = "✅" if n["event_type"] == "accepted" else "🔔"
            items_html += f'<div class="client-notif-box {box_cls}" id="notif-card-{n["id"]}"><span>{icon_txt} {n["body"]}</span><button type="button" onclick="dismissClientNotif({n["id"]})" style="float:right;background:none;border:none;font-size:1rem;font-weight:900;cursor:pointer;color:inherit;">✕</button></div>'
        client_notifs_html = f"""
        <div class="card-3d">
          <h2>🔔 Njoftimet e Terminit &amp; Llogarisë</h2>
          {items_html}
        </div>
        """

    reset_pwd_box_html = ""
    if logged_client and logged_client["password_reset_required"] == 1:
        reset_pwd_box_html = """
        <div class="card-3d" style="border: 2px solid #d97706; background: #fffbeb;">
          <h2 style="color:#92400e;">🔑 Vendos Password të Ri</h2>
          <p style="margin:0 0 8px; font-size:0.86rem; color:#78350f;">
            Admini ka kërkuar resetimin e password-it tuaj. Futni një password të ri (min. 6 karaktere) për të vazhduar.
          </p>
          <form method="POST">
            <input type="hidden" name="form_type" value="client_set_new_password">
            <input type="hidden" name="active_tab" value="book">
            <label for="new_client_password">Password-i i Ri *</label>
            <input type="password" name="new_client_password" id="new_client_password" minlength="6" required>
            <label for="confirm_client_password">Konfirmo Password-in e Ri *</label>
            <input type="password" name="confirm_client_password" id="confirm_client_password" minlength="6" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">
              💾 Ruaj Password-in e Ri
            </button>
          </form>
        </div>
        """

    if logged_client:
        main_ident = logged_client["instagram"] or logged_client["email"]
        client_auth_section = f"""
        <div class="client-session-bar">
          <div>
            <b>✅ Llogaria e Klientes:</b> {main_ident}<br>
            <span style="font-size:0.78rem; color:var(--muted);">Tel: {logged_client['phone']}</span>
          </div>
          <form method="POST" style="margin:0;">
            <input type="hidden" name="form_type" value="client_logout">
            <input type="hidden" name="active_tab" value="book">
            <button type="submit" class="btn-sm-3d btn-del">Dil</button>
          </form>
        </div>
        {reset_pwd_box_html}
        """
        booking_form_lock_note = ""
        booking_disabled_attr = ""
    else:
        reg_active = "active" if auth_mode == "register" else ""
        login_active = "active" if auth_mode == "login" else ""
        reg_display = "block" if auth_mode == "register" else "none"
        login_display = "block" if auth_mode == "login" else "none"

        m_ig_active = "active" if reg_method == "instagram" else ""
        m_em_active = "active" if reg_method == "email" else ""
        ig_field_disp = "block" if reg_method == "instagram" else "none"
        em_field_disp = "block" if reg_method == "email" else "none"
        ig_req_attr = "required" if reg_method == "instagram" else ""
        em_req_attr = "required" if reg_method == "email" else ""

        client_auth_section = f"""
        <div class="card-3d auth-gate-card" id="clientAuthGate">
          <h2>🔐 Regjistrohu për të Bërë Rezervim</h2>
          <p style="margin:0 0 8px; font-size:0.86rem; color:var(--muted); line-height:1.4;">
            Rezervimi nuk lejohet pa u regjistruar. Zgjidhni variantin me <b>Instagram</b> (pa email) ose me <b>Email</b> (pa Instagram), plus <b>telefonin</b> dhe <b>password-in</b>.
          </p>

          <div class="auth-mode-switch">
            <button type="button" id="btnAuthRegister" class="auth-mode-btn {reg_active}" onclick="setAuthMode('register')">
              ✍️ Regjistrim i Ri
            </button>
            <button type="button" id="btnAuthLogin" class="auth-mode-btn {login_active}" onclick="setAuthMode('login')">
              🔑 Kam Llogari (Kyçu)
            </button>
          </div>

          <form method="POST" id="clientRegisterForm" style="display:{reg_display};">
            <input type="hidden" name="form_type" value="client_register">
            <input type="hidden" name="reg_method" id="reg_method" value="{reg_method}">
            <input type="hidden" name="active_tab" value="book">

            <label>Zgjidh Variantën e Regjistrimit *</label>
            <div class="method-choice-row">
              <button type="button" id="chipMethodIg" class="method-chip {m_ig_active}" onclick="setRegMethod('instagram')">
                📸 Me Instagram
              </button>
              <button type="button" id="chipMethodEmail" class="method-chip {m_em_active}" onclick="setRegMethod('email')">
                ✉️ Me Email
              </button>
            </div>

            <div id="boxRegInstagram" style="display:{ig_field_disp};">
              <label for="reg_instagram">Llogaria e Instagramit *</label>
              <input type="text" name="reg_instagram" id="reg_instagram" autocomplete="username" autocapitalize="none" autocorrect="off" placeholder="p.sh. @sara.berisha" {ig_req_attr}>
              <div class="ig-autofill-row">
                <span id="igAutofillHint">Mbushja automatike aktivizohet kur prekni Me Instagram.</span>
                <button type="button" class="ig-mini-btn" onclick="syncInstagramFromDevice()">📲 Merr nga IG</button>
              </div>
            </div>

            <div id="boxRegEmail" style="display:{em_field_disp};">
              <label for="reg_email">Adresa e Emailit *</label>
              <input type="email" name="reg_email" id="reg_email" autocomplete="email" placeholder="p.sh. sara@email.com" {em_req_attr}>
            </div>

            <label for="reg_phone">Numri i Telefonit *</label>
            <input type="tel" name="reg_phone" id="reg_phone" autocomplete="tel" placeholder="p.sh. +383 44 123 456" required>

            <label for="reg_password">Fjalëkalimi (Password) *</label>
            <input type="password" name="reg_password" id="reg_password" autocomplete="new-password" minlength="6" placeholder="Minimumi 6 karaktere" required>

            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">
              ✅ Regjistrohu &amp; Zhblloko Rezervimin
            </button>
          </form>

          <form method="POST" id="clientLoginForm" style="display:{login_display};">
            <input type="hidden" name="form_type" value="client_login">
            <input type="hidden" name="active_tab" value="book">

            <label for="login_identifier">Instagrami ose Emaili *</label>
            <input type="text" name="login_identifier" id="login_identifier" autocomplete="username" placeholder="@llogaria_ig ose email@shembull.com" required>

            <label for="login_password">Fjalëkalimi (Password) *</label>
            <input type="password" name="login_password" id="login_password" autocomplete="current-password" required>

            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">
              🔓 Kyçu për të Rezervuar
            </button>
          </form>
        </div>
        """
        booking_form_lock_note = """
        <div class="alert alert-err" style="margin-bottom:8px;">
          ⛔ Rezervimi është i bllokuar derisa të regjistroheni (me Instagram ose Email) + telefon + password.
        </div>
        """
        booking_disabled_attr = "disabled"

    prefill_name = (logged_client["instagram"] or logged_client["email"]) if logged_client else ""
    prefill_phone = logged_client["phone"] if logged_client else ""

    inner = f"""
      <div id="liveToastBanner" class="live-toast-banner">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <b id="liveToastTitle" style="font-size:1.02rem;">🔔 NJOFTIM — GRANDE STUDIO</b>
          <button type="button" onclick="document.getElementById('liveToastBanner').style.display='none'" style="background:none; border:none; color:#fff; font-weight:900; font-size:1.15rem; cursor:pointer;">✕</button>
        </div>
        <div id="liveToastBody" style="font-size:0.88rem; margin-top:6px; line-height:1.45;"></div>
        <button type="button" onclick="window.location.reload()" style="margin-top:9px; width:100%; background:#fff; color:#be123c; border:none; border-radius:9px; padding:7px; font-weight:800; cursor:pointer;">
          🔄 Rifresko Tani
        </button>
      </div>

      <div id="installGuideModal" style="display:none; position:fixed; inset:0; background:rgba(20,4,8,0.72); z-index:9998; align-items:center; justify-content:center; padding:16px;">
        <div style="background:#fff; color:#261217; border-radius:18px; padding:16px; max-width:350px; width:100%; box-shadow:0 16px 40px rgba(0,0,0,0.45); border:1.5px solid #edd3da;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <b style="color:#72091d; font-size:1.02rem;" id="installModalTitle">📲 Instalo Grande Studio</b>
            <button type="button" onclick="closeInstallModal()" style="background:none; border:none; font-size:1.15rem; font-weight:800; color:#72091d; cursor:pointer;">✕</button>
          </div>
          <div id="installModalBody" style="font-size:0.88rem; line-height:1.55; background:#fdf5f7; padding:10px; border-radius:12px; border:1px solid #edd3da;"></div>
          <button type="button" onclick="closeInstallModal()" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px; padding:9px;">
            E kuptova
          </button>
        </div>
      </div>

      <header>
        <div class="header-inner">
          <div>
            <h1 class="brand-title">GRANDE STUDIO</h1>
            <p class="brand-sub">Thonj • Qerpikë • Vetulla</p>
          </div>
          <div class="header-mid">
            {ig_nav_html}
          </div>
          <div class="header-actions">
            <a href="{url_for('admin_panel')}" class="btn-3d btn-ghost-3d">🔒 Admin</a>
            <a href="{url_for('admin_reset_password')}" class="btn-3d btn-ghost-3d">🔑 Reset password</a>
          </div>
        </div>
      </header>

      <div class="container">
        {alert_html}
        {client_notifs_html}

        <div class="install-prompt-box" id="pwaInstallBanner" style="display:flex;">
          <div style="font-size:0.84rem; color:var(--vishnje-dark); line-height:1.35;">
            <b>📲 Instalo Grande Studio</b><br>
            Shtoje në ekranin kryesor (Wi-Fi ose 5G)!
          </div>
          <button type="button" id="pwaInstallBtn" class="btn-3d btn-vishnje-3d" style="padding:7px 11px; font-size:0.82rem; white-space:nowrap;">
            Instalo Tani
          </button>
        </div>

        <div class="combo-banner">
          {banner_art_html}
          <div class="combo-banner-content">
            <h3>✨ Thonj, Qerpikë &amp; Vetulla</h3>
            <p>Trajtime individuale ose paketa të kombinuara në një termin të vetëm te <b>Grande Studio</b>.</p>
            <div class="pillar-pills">
              <span class="pillar-pill">💅 Thonj</span>
              <span class="pillar-pill">👁️ Qerpikë</span>
              <span class="pillar-pill">🪄 Vetulla</span>
              <span class="pillar-pill">💎 Kombinim</span>
            </div>
          </div>
        </div>

        <div class="tabs-bar" role="tablist">
          <button type="button" class="tab-btn {'active' if active_tab == 'book' else ''}" data-tab="book" onclick="switchTab('book')">
            📅 Rezervo Termin
          </button>
          <button type="button" class="tab-btn {'active' if active_tab == 'beauty' else ''}" data-tab="beauty" onclick="switchTab('beauty')">
            💅 Katalogu &amp; Artikujt
          </button>
          <button type="button" class="tab-btn {'active' if active_tab == 'install' else ''}" data-tab="install" onclick="switchTab('install')">
            📱 iPhone &amp; Samsung
          </button>
          <button type="button" class="tab-btn {'active' if active_tab == 'info' else ''}" data-tab="info" onclick="switchTab('info')">
            🕰️ Orari &amp; Info
          </button>
        </div>

        <div id="tab-book" class="tab-panel {'active' if active_tab == 'book' else ''}">
          {client_auth_section}

          <div class="card-3d" id="bookingCardBox" style="{'opacity:0.65;' if not logged_client else ''}">
            <h2>Rezervo Terminin Tënd</h2>
            {booking_form_lock_note}

            <label>Filtro sipas Zonës</label>
            <div class="cat-filter-row" id="categoryFilterRow">
              <button type="button" class="cat-chip-3d active" data-cat="ALL" onclick="filterServicesByCat('ALL')">Të gjitha</button>
              <button type="button" class="cat-chip-3d" data-cat="Kombinim" onclick="filterServicesByCat('Kombinim')">Kombinim</button>
              <button type="button" class="cat-chip-3d" data-cat="Thonj" onclick="filterServicesByCat('Thonj')">Thonj</button>
              <button type="button" class="cat-chip-3d" data-cat="Qerpikë" onclick="filterServicesByCat('Qerpikë')">Qerpikë</button>
              <button type="button" class="cat-chip-3d" data-cat="Vetulla" onclick="filterServicesByCat('Vetulla')">Vetulla</button>
            </div>

            <form method="POST" id="bookingForm">
              <input type="hidden" name="form_type" value="booking">
              <input type="hidden" name="active_tab" value="book">

              <label for="service_id">1. Zgjidh Artikullin / Paketën</label>
              <select name="service_id" id="service_id" {booking_disabled_attr} required>
                {service_options}
              </select>

              <label for="appt_date">2. Zgjidh Datën</label>
              <input type="date" name="appt_date" id="appt_date" value="{today_str}" min="{today_str}" {booking_disabled_attr} required>

              <label>3. Zgjidh Orarin e Lirë</label>
              <div id="slotsContainer" class="slots">Duke ngarkuar oraret...</div>
              <input type="hidden" name="start_time" id="start_time" required>

              <div class="row-2">
                <div>
                  <label for="client_name">Emri / Llogaria</label>
                  <input type="text" name="client_name" id="client_name" value="{prefill_name}" placeholder="p.sh. Sara Berisha" {booking_disabled_attr} required>
                </div>
                <div>
                  <label for="client_phone">Numri i Telefonit</label>
                  <input type="tel" name="client_phone" id="client_phone" value="{prefill_phone}" readonly>
                </div>
              </div>

              <label for="notes">Detaje për Thonj, Qerpikë ose Vetulla</label>
              <textarea name="notes" id="notes" rows="2" placeholder="p.sh. thonj mandorle vishnje, lash lift, laminim vetullash..." {booking_disabled_attr}></textarea>

              <button type="submit" id="confirmBookingBtn" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:14px;" {booking_disabled_attr}>
                ✨ Konfirmo Terminin
              </button>
            </form>
          </div>
        </div>

        <div id="tab-beauty" class="tab-panel {'active' if active_tab == 'beauty' else ''}">
          <div class="card-3d">
            <h2>Artikujt: Thonj, Qerpikë &amp; Vetulla</h2>
            <div class="cat-filter-row" id="catalogFilterRow">
              <button type="button" class="cat-chip-3d active" data-bcat="ALL" onclick="filterCatalog('ALL')">Të gjitha</button>
              <button type="button" class="cat-chip-3d" data-bcat="Kombinim" onclick="filterCatalog('Kombinim')">Kombinime</button>
              <button type="button" class="cat-chip-3d" data-bcat="Thonj" onclick="filterCatalog('Thonj')">Thonj</button>
              <button type="button" class="cat-chip-3d" data-bcat="Qerpikë" onclick="filterCatalog('Qerpikë')">Qerpikë</button>
              <button type="button" class="cat-chip-3d" data-bcat="Vetulla" onclick="filterCatalog('Vetulla')">Vetulla</button>
            </div>

            <div class="svc-grid" id="beautyCardsGrid">
              {beauty_cards_html}
            </div>
          </div>
        </div>

        <div id="tab-install" class="tab-panel {'active' if active_tab == 'install' else ''}">
          <div class="card-3d">
            <h2>Instalimi në iPhone &amp; Samsung</h2>
            <div class="svc-grid">
              <div class="svc-card-3d">
                <div>
                  <span class="svc-tag">iOS / iPhone</span>
                  <div class="svc-title">Instalimi në Safari</div>
                  <div class="svc-desc">
                    1. Hap linkun e <b>Grande Studio</b> në <b>Safari</b>.<br>
                    2. Shtyp ikonën <b>Share</b> (katrori me shigjetë).<br>
                    3. Zgjidh <b>Add to Home Screen</b> dhe shtyp <b>Add</b>.
                  </div>
                </div>
              </div>
              <div class="svc-card-3d">
                <div>
                  <span class="svc-tag">Android / Samsung</span>
                  <div class="svc-title">Instalimi në Chrome / Samsung Internet</div>
                  <div class="svc-desc">
                    1. Hap linkun në <b>Google Chrome</b> ose <b>Samsung Internet</b>.<br>
                    2. Shtyp menynë <b>⋮</b> (ose <b>≡</b>).<br>
                    3. Zgjidh <b>Install app</b> ose <b>Add to Home screen</b>.
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div id="tab-info" class="tab-panel {'active' if active_tab == 'info' else ''}">
          <div class="card-3d">
            <h2>Orari &amp; Standardet — Grande Studio</h2>
            <p style="margin:0; color:var(--muted); font-size:0.92rem; line-height:1.55;">
              • <b>Orari i rezervimeve:</b> {WORK_START} – {WORK_END} (llogaritja e kohëzgjatjes bëhet automatikisht).<br>
              • <b>Higjiena &amp; Sterilizimi:</b> Veglat e thonjve, qerpikëve dhe vetullave sterilizohen pas çdo klienteje.<br>
              • <b>Instagram:</b> <a href="{IG_WEB_URL}" data-ig-app="{IG_APP_URL}" data-ig-web="{IG_WEB_URL}" onclick="openInstagramProfile(event)" style="color:var(--vishnje);font-weight:700;">@{IG_USERNAME}</a>
            </p>
          </div>
        </div>
      </div>

      <script>
        const IS_CLIENT_LOGGED_IN = {'true' if logged_client else 'false'};
        const clientSavedToken = '{client_remember_token_to_set or ""}';
        if (clientSavedToken) {{
          try {{ localStorage.setItem('grande_client_persistent_token', clientSavedToken); }} catch (e) {{}}
        }}

        (async function checkAdminAndClientAutoRedirect() {{
          try {{
            const adminTok = localStorage.getItem('grande_admin_persistent_token');
            if (adminTok) {{
              const res = await fetch('/api/admin/restore-session', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ token: adminTok }})
              }});
              if (res.ok) {{
                window.location.replace('/admin');
                return;
              }}
            }}
            
            if (!IS_CLIENT_LOGGED_IN) {{
              const cTok = localStorage.getItem('grande_client_persistent_token');
              if (cTok) {{
                const cres = await fetch('/api/client/restore-session', {{
                  method: 'POST',
                  headers: {{ 'Content-Type': 'application/json' }},
                  body: JSON.stringify({{ token: cTok }})
                }});
                if (cres.ok) {{
                  window.location.reload();
                }}
              }}
            }}
          }} catch (e) {{}}
        }})();

        if ('serviceWorker' in navigator) {{
          navigator.serviceWorker.addEventListener('message', (event) => {{
            if (event.data && event.data.type === 'PLAY_CHIME') {{
              playNotificationChime();
            }}
          }});
        }}

        async function autoRegisterPushNotifications() {{
          if ('Notification' in window && Notification.permission !== 'granted') {{
            try {{ await Notification.requestPermission(); }} catch (e) {{}}
          }}
          if ('serviceWorker' in navigator && 'PushManager' in window && IS_CLIENT_LOGGED_IN) {{
            try {{
              const reg = await navigator.serviceWorker.ready;
              const keyRes = await fetch('/api/push/public-key');
              const keyData = await keyRes.json();
              if (keyData.publicKey) {{
                let sub = await reg.pushManager.getSubscription();
                if (!sub) {{
                  sub = await reg.pushManager.subscribe({{
                    userVisibleOnly: true,
                    applicationServerKey: urlBase64ToUint8Array(keyData.publicKey)
                  }});
                }}
                await fetch('/api/client/push/subscribe', {{
                  method: 'POST',
                  headers: {{ 'Content-Type': 'application/json' }},
                  body: JSON.stringify(sub)
                }});
              }}
            }} catch (err) {{}}
          }}
        }}

        if (IS_CLIENT_LOGGED_IN) {{
          window.addEventListener('load', autoRegisterPushNotifications);
        }}

        async function dismissClientNotif(id) {{
          const el = document.getElementById('notif-card-' + id);
          if (el) {{
            el.style.transform = 'translateX(100%)';
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 250);
          }}
          await fetch('/api/client/notifications/read', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ id: id }})
          }});
          pollClientNotifications();
        }}

        document.addEventListener('touchstart', handleTouchStart, false);
        document.addEventListener('touchmove', handleTouchMove, false);
        document.addEventListener('touchend', handleTouchEnd, false);

        let xDown = null;
        let yDown = null;
        let activeNotifCard = null;

        function handleTouchStart(evt) {{
          const card = evt.target.closest('.client-notif-box');
          if (card) {{
            xDown = evt.touches[0].clientX;
            yDown = evt.touches[0].clientY;
            activeNotifCard = card;
          }}
        }}

        function handleTouchMove(evt) {{
          if (!xDown || !yDown || !activeNotifCard) return;
          let xUp = evt.touches[0].clientX;
          let yUp = evt.touches[0].clientY;
          let xDiff = xUp - xDown;
          let yDiff = yUp - yDown;
          if (Math.abs(xDiff) > Math.abs(yDiff) && xDiff > 0) {{
            activeNotifCard.style.transform = `translateX(${{xDiff}}px)`;
          }}
        }}

        function handleTouchEnd(evt) {{
          if (!xDown || !activeNotifCard) return;
          let xUp = evt.changedTouches[0].clientX;
          let xDiff = xUp - xDown;
          if (xDiff > 110) {{
            activeNotifCard.style.transform = 'translateX(100%)';
            activeNotifCard.style.opacity = '0';
            const matchId = activeNotifCard.id.replace('notif-card-', '');
            setTimeout(() => activeNotifCard.remove(), 250);
            if (matchId) {{
              fetch('/api/client/notifications/read', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ id: Number(matchId) }})
              }}).then(() => pollClientNotifications());
            }}
          }} else {{
            activeNotifCard.style.transform = 'translateX(0)';
          }}
          xDown = null;
          yDown = null;
          activeNotifCard = null;
        }}

        let seenClientNotifIds = new Set();
        async function pollClientNotifications() {{
          if (!IS_CLIENT_LOGGED_IN) return;
          try {{
            const res = await fetch('/api/client/notifications');
            if (!res.ok) return;
            const data = await res.json();
            if (data.blocked) {{
              window.location.reload();
              return;
            }}
            const list = data.notifications || [];
            if (!seenClientNotifIds.size) {{
              list.forEach(item => seenClientNotifIds.add(item.id));
              if (data.reset_required) {{
                window.location.reload();
              }}
              return;
            }}
            const unreadNew = list.filter(item => item.is_read === 0 && !seenClientNotifIds.has(item.id));
            if (unreadNew.length > 0) {{
              unreadNew.forEach(item => seenClientNotifIds.add(item.id));
              const latest = unreadNew[0];
              playNotificationChime();
              if (navigator.vibrate) navigator.vibrate([300, 150, 300]);
              const toast = document.getElementById('liveToastBanner');
              const toastTitle = document.getElementById('liveToastTitle');
              const toastBody = document.getElementById('liveToastBody');
              if (toast && toastTitle && toastBody) {{
                toastTitle.textContent = latest.title || '🔔 NJOFTIM — GRANDE STUDIO';
                toastBody.textContent = latest.body;
                toast.style.display = 'block';
              }}
              if (data.reset_required) {{
                setTimeout(() => window.location.reload(), 1500);
              }}
            }}
          }} catch (e) {{}}
        }}

        if (IS_CLIENT_LOGGED_IN) {{
          pollClientNotifications();
          setInterval(pollClientNotifications, 4000);
        }}

        function formatIgUsername(raw) {{
          if (!raw) return '';
          let s = String(raw).trim();
          const matchUrl = s.match(/instagram\\.com\\/([^/?#&]+)/i);
          if (matchUrl && matchUrl[1]) s = matchUrl[1];
          s = s.replace(/^@+/, '').trim();
          if (!s || s.includes(' ') || s.length > 35) return '';
          return '@' + s.toLowerCase();
        }}

        function saveDetectedInstagram(val) {{
          const formatted = formatIgUsername(val);
          if (!formatted) return '';
          try {{ localStorage.setItem('grande_studio_ig_user', formatted); }} catch (e) {{}}
          return formatted;
        }}

        function getStoredInstagram() {{
          try {{
            const params = new URLSearchParams(window.location.search);
            const fromQuery = formatIgUsername(params.get('ig') || params.get('instagram') || params.get('username') || '');
            if (fromQuery) {{
              saveDetectedInstagram(fromQuery);
              return fromQuery;
            }}
            const saved = formatIgUsername(localStorage.getItem('grande_studio_ig_user') || '');
            if (saved) return saved;
          }} catch (e) {{}}
          return '';
        }}

        async function autofillInstagramField(fromReturnAfterIgApp) {{
          const inpIg = document.getElementById('reg_instagram');
          const hintEl = document.getElementById('igAutofillHint');
          if (!inpIg) return;

          const stored = getStoredInstagram();
          if (stored) {{
            inpIg.value = stored;
            if (hintEl) hintEl.textContent = 'U plotësua automatikisht (' + stored + '). Plotëso vetëm telefonin & password-in.';
            return;
          }}

          if (fromReturnAfterIgApp && navigator.clipboard && navigator.clipboard.readText) {{
            try {{
              const clipText = await navigator.clipboard.readText();
              const clipIg = formatIgUsername(clipText);
              if (clipIg) {{
                inpIg.value = clipIg;
                saveDetectedInstagram(clipIg);
                if (hintEl) hintEl.textContent = 'U plotësua automatikisht (' + clipIg + '). Plotëso vetëm telefonin & password-in.';
                return;
              }}
            }} catch (e) {{}}
          }}

          if (hintEl) {{
            hintEl.textContent = 'Nëse nuk plotësohet vetë, preke fushën për autofill ose "Merr nga IG".';
          }}
        }}

        function syncInstagramFromDevice() {{
          const inpIg = document.getElementById('reg_instagram');
          if (inpIg && inpIg.value.trim()) {{
            const saved = saveDetectedInstagram(inpIg.value);
            if (saved) {{
              inpIg.value = saved;
              autofillInstagramField(false);
              return;
            }}
          }}
          try {{
            sessionStorage.setItem('grande_ig_sync_pending', '1');
            window.location.href = 'instagram://app';
          }} catch (e) {{}}
          setTimeout(() => autofillInstagramField(true), 800);
        }}

        document.addEventListener('visibilitychange', () => {{
          if (!document.hidden) {{
            try {{
              if (sessionStorage.getItem('grande_ig_sync_pending') === '1') {{
                sessionStorage.removeItem('grande_ig_sync_pending');
                autofillInstagramField(true);
              }}
            }} catch (e) {{}}
            pollClientNotifications();
          }}
        }});

        function setAuthMode(mode) {{
          const regForm = document.getElementById('clientRegisterForm');
          const loginForm = document.getElementById('clientLoginForm');
          const btnReg = document.getElementById('btnAuthRegister');
          const btnLog = document.getElementById('btnAuthLogin');
          if (!regForm || !loginForm) return;
          if (mode === 'login') {{
            regForm.style.display = 'none';
            loginForm.style.display = 'block';
            btnReg.classList.remove('active');
            btnLog.classList.add('active');
          }} else {{
            regForm.style.display = 'block';
            loginForm.style.display = 'none';
            btnReg.classList.add('active');
            btnLog.classList.remove('active');
          }}
        }}

        function setRegMethod(method) {{
          const methodInput = document.getElementById('reg_method');
          const chipIg = document.getElementById('chipMethodIg');
          const chipEm = document.getElementById('chipMethodEmail');
          const boxIg = document.getElementById('boxRegInstagram');
          const boxEm = document.getElementById('boxRegEmail');
          const inpIg = document.getElementById('reg_instagram');
          const inpEm = document.getElementById('reg_email');
          if (!methodInput) return;

          if (method === 'email') {{
            methodInput.value = 'email';
            chipEm.classList.add('active');
            chipIg.classList.remove('active');
            boxEm.style.display = 'block';
            boxIg.style.display = 'none';
            inpEm.required = true;
            inpIg.required = false;
            inpIg.value = '';
          }} else {{
            methodInput.value = 'instagram';
            chipIg.classList.add('active');
            chipEm.classList.remove('active');
            boxIg.style.display = 'block';
            boxEm.style.display = 'none';
            inpIg.required = true;
            inpEm.required = false;
            inpEm.value = '';
            autofillInstagramField(false);
          }}
        }}

        const regIgInput = document.getElementById('reg_instagram');
        if (regIgInput) {{
          regIgInput.addEventListener('change', () => {{
            const f = saveDetectedInstagram(regIgInput.value);
            if (f) regIgInput.value = f;
          }});
        }}

        let deferredPrompt = null;
        const installBanner = document.getElementById('pwaInstallBanner');
        const installBtn = document.getElementById('pwaInstallBtn');
        const installModal = document.getElementById('installGuideModal');
        const installModalTitle = document.getElementById('installModalTitle');
        const installModalBody = document.getElementById('installModalBody');

        const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
        const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;

        if (isStandalone && installBanner) {{
          installBanner.style.display = 'none';
        }}

        window.addEventListener('beforeinstallprompt', (e) => {{
          e.preventDefault();
          deferredPrompt = e;
          if (!isStandalone && installBanner) {{
            installBanner.style.display = 'flex';
          }}
        }});

        window.addEventListener('appinstalled', () => {{
          deferredPrompt = null;
          if (installBanner) installBanner.style.display = 'none';
          closeInstallModal();
        }});

        function closeInstallModal() {{
          if (installModal) installModal.style.display = 'none';
        }}

        if (installBtn) {{
          installBtn.addEventListener('click', async () => {{
            if (deferredPrompt) {{
              deferredPrompt.prompt();
              const choice = await deferredPrompt.userChoice;
              deferredPrompt = null;
              if (choice && choice.outcome === 'accepted' && installBanner) {{
                installBanner.style.display = 'none';
              }}
              return;
            }}

            if (isIOS) {{
              installModalTitle.textContent = '📲 Instalo në iPhone (Safari)';
              installModalBody.innerHTML =
                '1. Shtypni butonin <b>Share</b> (katrori me shigjetë lart) në Safari.<br>' +
                '2. Zgjidhni <b>Add to Home Screen</b>.<br>' +
                '3. Shtypni <b>Add</b> lart djathtas.';
            }} else {{
              installModalTitle.textContent = '📲 Instalo në Samsung / Android';
              installModalBody.innerHTML =
                '1. Shtypni menynë <b>⋮</b> lart djathtas në Chrome (ose <b>≡</b> në Samsung Internet).<br>' +
                '2. Zgjidhni <b>Install app</b> ose <b>Add to Home screen</b>.<br>' +
                '3. Konfirmoni me <b>Install / Add</b>.';
            }}
            if (installModal) installModal.style.display = 'flex';
          }});
        }}

        function switchTab(tabName) {{
          document.querySelectorAll('.tab-btn').forEach(btn => {{
            btn.classList.toggle('active', btn.getAttribute('data-tab') === tabName);
          }});
          document.querySelectorAll('.tab-panel').forEach(panel => {{
            panel.classList.toggle('active', panel.id === 'tab-' + tabName);
          }});
        }}

        const svcEl = document.getElementById('service_id');
        const dateEl = document.getElementById('appt_date');
        const slotsEl = document.getElementById('slotsContainer');
        const startInput = document.getElementById('start_time');
        const allOptions = Array.from(svcEl.options).map(opt => ({{
          value: opt.value,
          cat: opt.getAttribute('data-cat'),
          text: opt.text
        }}));

        function enforceBookingLockState() {{
          if (IS_CLIENT_LOGGED_IN) return;
          svcEl.disabled = true;
          dateEl.disabled = true;
          const cname = document.getElementById('client_name');
          const cnotes = document.getElementById('notes');
          const cbtn = document.getElementById('confirmBookingBtn');
          if (cname) cname.disabled = true;
          if (cnotes) cnotes.disabled = true;
          if (cbtn) cbtn.disabled = true;
        }}

        function filterServicesByCat(cat) {{
          document.querySelectorAll('#categoryFilterRow .cat-chip-3d').forEach(b => {{
            b.classList.toggle('active', b.getAttribute('data-cat') === cat);
          }});
          const filtered = cat === 'ALL' ? allOptions : allOptions.filter(o => o.cat === cat);
          svcEl.innerHTML = '';
          filtered.forEach(o => {{
            const opt = document.createElement('option');
            opt.value = o.value;
            opt.setAttribute('data-cat', o.cat);
            opt.textContent = o.text;
            svcEl.appendChild(opt);
          }});
          loadSlots();
        }}

        function filterCatalog(cat) {{
          document.querySelectorAll('#catalogFilterRow .cat-chip-3d').forEach(b => {{
            b.classList.toggle('active', b.getAttribute('data-bcat') === cat);
          }});
          document.querySelectorAll('#beautyCardsGrid .svc-card-3d').forEach(card => {{
            const cardCat = card.getAttribute('data-cat-card');
            card.style.display = (cat === 'ALL' || cardCat === cat) ? 'flex' : 'none';
          }});
        }}

        function selectServiceAndBook(serviceId) {{
          filterServicesByCat('ALL');
          svcEl.value = String(serviceId);
          switchTab('book');
          loadSlots();
          const scroller = document.getElementById('iphoneScrollArea');
          if (scroller) scroller.scrollTo({{ top: 0, behavior: 'smooth' }});
        }}

        async function loadSlots() {{
          startInput.value = '';
          if (!svcEl.value) {{
            slotsEl.innerHTML = '<span style="color:#b41e2d;font-size:0.86rem;">Nuk ka artikull të zgjedhur.</span>';
            return;
          }}
          slotsEl.innerHTML = '<span style="color:#6e4e57;font-size:0.86rem;">Po kontrollohen oraret e lira...</span>';
          const res = await fetch(`/api/slots?date=${{encodeURIComponent(dateEl.value)}}&service_id=${{encodeURIComponent(svcEl.value)}}`);
          const data = await res.json();
          slotsEl.innerHTML = '';

          if (!data.slots || data.slots.length === 0) {{
            slotsEl.innerHTML = '<span style="color:#b41e2d;font-size:0.86rem;">Nuk ka orare të lira për këtë datë dhe kohëzgjatje.</span>';
            return;
          }}

          data.slots.forEach(slot => {{
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'slot-3d';
            btn.textContent = slot;
            if (!IS_CLIENT_LOGGED_IN) {{
              btn.disabled = true;
              btn.style.opacity = '0.55';
            }}
            btn.onclick = () => {{
              if (!IS_CLIENT_LOGGED_IN) {{
                alert('Ju lutem regjistrohuni ose kyçuni para se të bëni rezervim!');
                return;
              }}
              document.querySelectorAll('.slot-3d').forEach(b => b.classList.remove('active'));
              btn.classList.add('active');
              startInput.value = slot;
            }};
            slotsEl.appendChild(btn);
          }});
        }}

        svcEl.addEventListener('change', loadSlots);
        dateEl.addEventListener('change', loadSlots);
        document.getElementById('bookingForm').addEventListener('submit', (e) => {{
          if (!IS_CLIENT_LOGGED_IN) {{
            e.preventDefault();
            alert('Ju lutem regjistrohuni ose kyçuni para se të bëni rezervim!');
            return;
          }}
          if (!startInput.value) {{
            e.preventDefault();
            alert('Ju lutem zgjidhni një orar të lirë!');
          }}
        }});

        enforceBookingLockState();
        loadSlots();
      </script>
    """
    html_out = render_iphone_shell("Grande Studio — Thonj, Qerpikë & Vetulla", inner)
    resp = Response(html_out, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    if client_remember_token_to_set:
        resp.set_cookie(
            CLIENT_REMEMBER_COOKIE,
            client_remember_token_to_set,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
        )
    return resp


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    init_db()
    if is_admin_authenticated():
        return redirect(url_for("admin_panel"))

    acc = get_admin_account()
    ig_nav_html = get_ig_nav_button_html()
    err = None
    client_ip = request.remote_addr or "local"

    if acc is None:
        if request.method == "POST":
            email = request.form.get("reg_email", "").strip()
            pwd = request.form.get("reg_password", "")
            pwd2 = request.form.get("reg_password_confirm", "")

            if not email or "@" not in email:
                err = "Ju lutem shkruani një adresë të vlefshme emaili."
            elif len(pwd) < 6:
                err = "Fjalëkalimi duhet të ketë të paktën 6 karaktere."
            elif pwd != pwd2:
                err = "Fjalëkalimet nuk përputhen."
            else:
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO admin_account (id, email, password_hash, updated_at)
                    VALUES (1, ?, ?, ?)
                    """,
                    (email, generate_password_hash(pwd), datetime.now().isoformat()),
                )
                conn.commit()
                conn.close()
                tok = create_admin_remember_token()
                session.clear()
                session["admin_logged_in"] = True
                session["admin_token"] = tok
                session.permanent = True
                resp = redirect(url_for("admin_panel"))
                resp.set_cookie(
                    ADMIN_REMEMBER_COOKIE,
                    tok,
                    max_age=COOKIE_MAX_AGE,
                    httponly=True,
                    samesite="Lax",
                )
                return resp

        alert_html = f'<div class="alert alert-err">{err}</div>' if err else ""
        inner = f"""
          <header>
            <div class="header-inner">
              <div>
                <h1 class="brand-title">GRANDE STUDIO</h1>
                <p class="brand-sub">Regjistrim Admin</p>
              </div>
              <div class="header-mid">
                {ig_nav_html}
              </div>
              <div class="header-actions">
                <a href="{url_for('index')}" class="btn-3d btn-ghost-3d">← Klienti</a>
              </div>
            </div>
          </header>
          <div class="container">
            {alert_html}
            <div class="card-3d">
              <h2>Regjistrimi i Pronarit (Admin)</h2>
              <p style="margin:0 0 10px; font-size:0.88rem; color:var(--muted); line-height:1.4;">
                Regjistroni emailin dhe fjalëkalimin tuaj. Emaili shërben që në rast harrese të fjalëkalimit, resetimi të dërgohet direkt në emailin tuaj.
              </p>
              <form method="POST">
                <label for="reg_email">Emaili i Pronarit</label>
                <input type="email" name="reg_email" id="reg_email" placeholder="p.sh. pronari@grandestudio.com" required>

                <label for="reg_password">Fjalëkalimi</label>
                <input type="password" name="reg_password" id="reg_password" required>

                <label for="reg_password_confirm">Konfirmo Fjalëkalimin</label>
                <input type="password" name="reg_password_confirm" id="reg_password_confirm" required>

                <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:14px;">
                  Ruaj Llogarinë &amp; Hyj
                </button>
              </form>
            </div>
          </div>
        """
        return render_iphone_shell("Regjistrim Admin — Grande Studio", inner)

    if request.method == "POST":
        locked, wait_sec = is_login_locked(client_ip)
        if locked:
            err = f"Shumë tentativa të dështuara. Provoni sërish pas {wait_sec} sekondash."
        else:
            email_in = request.form.get("email", "").strip().lower()
            pwd_in = request.form.get("password", "")

            if email_in == acc["email"].strip().lower() and check_password_hash(acc["password_hash"], pwd_in):
                clear_failed_login(client_ip)
                tok = create_admin_remember_token()
                session.clear()
                session["admin_logged_in"] = True
                session["admin_token"] = tok
                session.permanent = True
                resp = redirect(url_for("admin_panel"))
                resp.set_cookie(
                    ADMIN_REMEMBER_COOKIE,
                    tok,
                    max_age=COOKIE_MAX_AGE,
                    httponly=True,
                    samesite="Lax",
                )
                return resp
            record_failed_login(client_ip)
            err = "Emaili ose fjalëkalimi është i pasaktë."

    err_html = f'<div class="alert alert-err">{err}</div>' if err else ""

    inner = f"""
      <header>
        <div class="header-inner">
          <div>
            <h1 class="brand-title">GRANDE STUDIO</h1>
            <p class="brand-sub">Hyrja e pronarit</p>
          </div>
          <div class="header-mid">
            {ig_nav_html}
          </div>
          <div class="header-actions">
            <a href="{url_for('index')}" class="btn-3d btn-ghost-3d">← Klienti</a>
            <a href="{url_for('admin_reset_password')}" class="btn-3d btn-ghost-3d">🔑 Reset password</a>
          </div>
        </div>
      </header>
      <div class="container">
        {err_html}
        <div class="card-3d">
          <h2>Kyçu në Panelin e Adminit</h2>
          <form method="POST">
            <label for="email">Emaili i Regjistruar</label>
            <input type="email" name="email" id="email" required>

            <label for="password">Fjalëkalimi</label>
            <input type="password" name="password" id="password" required>

            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:14px;">
              Kyçu
            </button>
          </form>
        </div>
      </div>
      <script>
        (async function tryAutoRestoreAdmin() {{
          try {{
            const savedTok = localStorage.getItem('grande_admin_persistent_token');
            if (!savedTok) return;
            const res = await fetch('/api/admin/restore-session', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ token: savedTok }})
            }});
            if (res.ok) {{
              window.location.replace('/admin');
            }} else {{
              localStorage.removeItem('grande_admin_persistent_token');
            }}
          }} catch (e) {{}}
        }})();
      </script>
    """
    return render_iphone_shell("Hyrja Admin — Grande Studio", inner)


@app.route("/admin/reset-password", methods=["GET", "POST"])
def admin_reset_password():
    init_db()
    acc = get_admin_account()
    ig_nav_html = get_ig_nav_button_html()
    msg = None
    msg_type = "ok"

    if acc is None:
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        entered_email = request.form.get("reset_email", "").strip().lower()
        if entered_email != acc["email"].strip().lower():
            msg = "Ky email nuk përputhet me emailin e regjistruar të pronarit."
            msg_type = "err"
        else:
            new_pwd = generate_temp_password(12)
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "UPDATE admin_account SET password_hash = ?, updated_at = ? WHERE id = 1",
                (generate_password_hash(new_pwd), datetime.now().isoformat()),
            )
            cur.execute("DELETE FROM admin_sessions")
            conn.commit()
            conn.close()
            session.pop("admin_logged_in", None)
            session.pop("admin_token", None)

            sent, info_txt = send_reset_email(acc["email"], new_pwd)
            msg = info_txt
            msg_type = "ok" if sent else "err"

    alert_html = ""
    if msg:
        cls = "alert-ok" if msg_type == "ok" else "alert-err"
        alert_html = f'<div class="alert {cls}">{msg}</div>'

    inner = f"""
      <header>
        <div class="header-inner">
          <div>
            <h1 class="brand-title">GRANDE STUDIO</h1>
            <p class="brand-sub">Reset fjalëkalimi</p>
          </div>
          <div class="header-mid">
            {ig_nav_html}
          </div>
          <div class="header-actions">
            <a href="{url_for('index')}" class="btn-3d btn-ghost-3d">← Klienti</a>
            <a href="{url_for('admin_login')}" class="btn-3d btn-ghost-3d">🔒 Admin</a>
          </div>
        </div>
      </header>
      <div class="container">
        {alert_html}
        <div class="card-3d">
          <h2>Reset Password në Email</h2>
          <p style="margin:0 0 10px; font-size:0.88rem; color:var(--muted); line-height:1.4;">
            Shkruani emailin që keni regjistruar si pronar. Fjalëkalimi i ri do të gjenerohet dhe do të dërgohet në atë email.
          </p>
          <form method="POST">
            <label for="reset_email">Emaili i Regjistruar</label>
            <input type="email" name="reset_email" id="reset_email" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:14px;">
              Dërgo Fjalëkalimin e Ri në Email
            </button>
          </form>
        </div>
      </div>
    """
    return render_iphone_shell("Reset Password — Grande Studio", inner)


@app.route("/admin/logout")
def admin_logout():
    cookie_tok = request.cookies.get(ADMIN_REMEMBER_COOKIE, "") or session.get("admin_token", "")
    delete_admin_remember_token(cookie_tok)
    session.pop("admin_logged_in", None)
    session.pop("admin_token", None)
    html_out = f"""
    <!doctype html>
    <html lang="sq" translate="no">
    <head>
      <meta charset="utf-8">
      <meta name="google" content="notranslate">
      <title>Duke dalë...</title>
    </head>
    <body>
      <script>
        try {{
          localStorage.removeItem('grande_admin_persistent_token');
        }} catch (e) {{}}
        window.location.replace("{url_for('admin_login')}");
      </script>
    </body>
    </html>
    """
    resp = Response(html_out, mimetype="text/html")
    resp.delete_cookie(ADMIN_REMEMBER_COOKIE)
    return resp


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel():
    init_db()
    acc = get_admin_account()
    ig_nav_html = get_ig_nav_button_html()
    conn = get_db()
    cur = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")
    feedback_msg = None
    feedback_type = "ok"
    show_clients_panel = request.form.get("show_clients") == "1"

    active_admin_token = session.get("admin_token") or request.cookies.get(ADMIN_REMEMBER_COOKIE, "")
    if not active_admin_token:
        active_admin_token = create_admin_remember_token()
        session["admin_token"] = active_admin_token
        session.permanent = True

    if request.method == "POST":
        action = request.form.get("action")

        if action == "toggle_block_client":
            show_clients_panel = True
            cid = request.form.get("client_id", type=int)
            if cid:
                cur.execute("SELECT is_blocked FROM client_accounts WHERE id = ?", (cid,))
                crow = cur.fetchone()
                if crow:
                    next_blk = 0 if crow["is_blocked"] == 1 else 1
                    cur.execute("UPDATE client_accounts SET is_blocked = ? WHERE id = ?", (next_blk, cid))
                    conn.commit()
                    feedback_msg = "Klientja u bllokua." if next_blk == 1 else "Klientja u zhbllokua."
                    feedback_type = "ok"

        elif action == "delete_client":
            show_clients_panel = True
            cid = request.form.get("client_id", type=int)
            if cid:
                cur.execute("DELETE FROM client_accounts WHERE id = ?", (cid,))
                conn.commit()
                feedback_msg = "Llogaria e klientes u fshi me sukses."
                feedback_type = "ok"

        elif action == "reset_client_password":
            show_clients_panel = True
            cid = request.form.get("client_id", type=int)
            if cid:
                cur.execute("SELECT auth_method, instagram, email FROM client_accounts WHERE id = ?", (cid,))
                c_acc = cur.fetchone()
                if c_acc:
                    cur.execute("UPDATE client_accounts SET password_reset_required = 1 WHERE id = ?", (cid,))
                    conn.commit()

                    dest_label = ""
                    if c_acc["auth_method"] == "instagram" and c_acc["instagram"]:
                        dest_label = f"Instagram ({c_acc['instagram']})"
                        notify_client_message(
                            cid,
                            "🔑 Reset Password — Grande Studio",
                            MSG_RESET_PWD,
                            event_type="reset_password"
                        )
                    elif c_acc["auth_method"] == "email" and c_acc["email"]:
                        dest_label = f"Email ({c_acc['email']})"
                        send_client_reset_email(c_acc["email"])
                        notify_client_message(
                            cid,
                            "🔑 Reset Password — Grande Studio",
                            MSG_RESET_PWD,
                            event_type="reset_password"
                        )
                    else:
                        dest_label = "llogarinë e klientes"
                        notify_client_message(
                            cid,
                            "🔑 Reset Password — Grande Studio",
                            MSG_RESET_PWD,
                            event_type="reset_password"
                        )

                    feedback_msg = f"Kërkesa për reset password u dërgua te {dest_label}. Kur klientja të klikojë njoftimin, do t'i shfaqet dritarja për fjalëkalim të ri."
                    feedback_type = "ok"

        elif action == "mark_seen":
            appt_id = request.form.get("appt_id", type=int)
            if appt_id:
                cur.execute("SELECT client_id FROM appointments WHERE id = ?", (appt_id,))
                row_a = cur.fetchone()
                c_id = row_a["client_id"] if row_a else None
                cur.execute("UPDATE appointments SET is_new = 0, status = 'Konfirmuar' WHERE id = ?", (appt_id,))
                conn.commit()
                notify_client_appointment_decision(c_id, accepted=True)
                feedback_msg = "Termini u konfirmua dhe klientes iu dërgua njoftimi me zë."
                feedback_type = "ok"

        elif action == "mark_all_seen":
            cur.execute("SELECT DISTINCT client_id FROM appointments WHERE is_new = 1 AND status = 'Konfirmuar' AND client_id IS NOT NULL")
            c_rows = cur.fetchall()
            cur.execute("UPDATE appointments SET is_new = 0 WHERE is_new = 1")
            conn.commit()
            for cr in c_rows:
                notify_client_appointment_decision(cr["client_id"], accepted=True)
            feedback_msg = "Të gjitha rezervimet e reja u konfirmuan dhe klientet u njoftuan."
            feedback_type = "ok"

        elif action == "test_push":
            send_push_to_admins(
                title="🔔 Test Njoftimi — Grande Studio",
                body="Njoftimet me zë dhe banner po funksionojnë edhe me ekran të bllokuar!",
                url="/admin?play_sound=1"
            )
            feedback_msg = "Njoftimi testues u dërgua në telefon!"
            feedback_type = "ok"

        elif action == "change_password":
            curr_pwd = request.form.get("current_password", "")
            new_pwd = request.form.get("new_password", "")
            conf_pwd = request.form.get("confirm_password", "")

            if not check_password_hash(acc["password_hash"], curr_pwd):
                feedback_msg = "Fjalëkalimi aktual është i pasaktë."
                feedback_type = "err"
            elif len(new_pwd) < 6:
                feedback_msg = "Fjalëkalimi i ri duhet të ketë të paktën 6 karaktere."
                feedback_type = "err"
            elif new_pwd != conf_pwd:
                feedback_msg = "Konfirmimi i fjalëkalimit të ri nuk përputhet."
                feedback_type = "err"
            else:
                cur.execute(
                    "UPDATE admin_account SET password_hash = ?, updated_at = ? WHERE id = 1",
                    (generate_password_hash(new_pwd), datetime.now().isoformat()),
                )
                conn.commit()
                acc = get_admin_account()
                feedback_msg = "Fjalëkalimi u ndryshua dhe u ruajt me sukses."
                feedback_type = "ok"

        elif action == "update_service":
            svc_id = request.form.get("svc_id", type=int)
            s_cat = request.form.get("s_cat", "Thonj").strip()
            s_name = request.form.get("s_name", "").strip()
            s_dur = request.form.get("s_dur", type=int) or 60
            s_price = request.form.get("s_price", type=float) or 0.0
            s_desc = request.form.get("s_desc", "").strip()
            if svc_id and s_name:
                cur.execute(
                    """
                    UPDATE services
                    SET category = ?, name = ?, duration_min = ?, price = ?, description = ?
                    WHERE id = ?
                    """,
                    (s_cat, s_name, s_dur, s_price, s_desc, svc_id),
                )
                conn.commit()
                feedback_msg = f"Artikulli '{s_name}' u përditësua dhe u ruajt."
                feedback_type = "ok"

        elif action == "add_service":
            s_cat = request.form.get("s_cat", "Thonj").strip()
            s_name = request.form.get("s_name", "").strip()
            s_dur = request.form.get("s_dur", type=int) or 60
            s_price = request.form.get("s_price", type=float) or 0.0
            s_desc = request.form.get("s_desc", "").strip()
            if s_name:
                cur.execute(
                    "INSERT INTO services (category, name, duration_min, price, description) VALUES (?, ?, ?, ?, ?)",
                    (s_cat, s_name, s_dur, s_price, s_desc),
                )
                conn.commit()
                feedback_msg = f"Artikulli i ri '{s_name}' u shtua dhe u ruajt."
                feedback_type = "ok"

        elif action == "delete_service":
            svc_id = request.form.get("svc_id", type=int)
            if svc_id:
                cur.execute("DELETE FROM services WHERE id = ?", (svc_id,))
                conn.commit()
                feedback_msg = "Artikulli u hoq me sukses."
                feedback_type = "ok"

        elif action == "block_slot":
            b_date = request.form.get("block_date", today_str)
            b_start = request.form.get("block_start", "09:00")
            b_end = request.form.get("block_end", "10:00")
            b_reason = request.form.get("block_reason", "Bllokuar nga Grande Studio").strip() or "Bllokuar"
            cur.execute("SELECT id FROM services LIMIT 1")
            first_svc = cur.fetchone()
            svc_id = first_svc["id"] if first_svc else None
            cur.execute(
                """
                INSERT INTO appointments
                (client_name, client_phone, service_id, appt_date, start_time, end_time, notes, status, is_new, created_at)
                VALUES (?, '-', ?, ?, ?, ?, ?, 'Bllokuar', 0, ?)
                """,
                (b_reason, svc_id, b_date, b_start, b_end, "Orar i bllokuar", datetime.now().isoformat()),
            )
            conn.commit()
            feedback_msg = "Orari u bllokua dhe u ruajt."
            feedback_type = "ok"

        elif action == "set_status":
            appt_id = request.form.get("appt_id", type=int)
            new_status = request.form.get("status", "Konfirmuar")
            if appt_id and new_status in ("Konfirmuar", "Anuluar", "Bllokuar"):
                cur.execute("SELECT client_id, status FROM appointments WHERE id = ?", (appt_id,))
                row_a = cur.fetchone()
                c_id = row_a["client_id"] if row_a else None
                cur.execute("UPDATE appointments SET status = ?, is_new = 0 WHERE id = ?", (new_status, appt_id))
                conn.commit()
                if new_status == "Konfirmuar":
                    notify_client_appointment_decision(c_id, accepted=True)
                elif new_status == "Anuluar":
                    notify_client_appointment_decision(c_id, accepted=False)
                feedback_msg = "Statusi i terminit u përditësua dhe klientja u njoftua."
                feedback_type = "ok"

        elif action == "delete_appt":
            appt_id = request.form.get("appt_id", type=int)
            if appt_id:
                cur.execute("SELECT client_id, status FROM appointments WHERE id = ?", (appt_id,))
                row_a = cur.fetchone()
                c_id = row_a["client_id"] if row_a else None
                st = row_a["status"] if row_a else ""
                cur.execute("DELETE FROM appointments WHERE id = ?", (appt_id,))
                conn.commit()
                if st != "Bllokuar" and c_id:
                    notify_client_appointment_decision(c_id, accepted=False)
                feedback_msg = "Termini u fshi dhe klientes iu dërgua njoftimi."
                feedback_type = "ok"

    cur.execute(
        """
        SELECT id, title, body, created_at
        FROM admin_notifications
        WHERE is_read = 0
        ORDER BY id DESC
        """
    )
    admin_notifs = cur.fetchall()

    cur.execute(
        """
        SELECT a.*, s.name AS service_name, s.category, s.price
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        WHERE a.is_new = 1 AND a.status = 'Konfirmuar'
        ORDER BY a.id DESC
        """
    )
    new_appts = cur.fetchall()

    cur.execute(
        """
        SELECT a.*, s.name AS service_name, s.category, s.price
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        ORDER BY a.is_new DESC, a.appt_date ASC, a.start_time ASC
        """
    )
    appts = cur.fetchall()

    cur.execute("SELECT * FROM services ORDER BY id")
    services = cur.fetchall()

    cur.execute("SELECT * FROM client_accounts ORDER BY id DESC")
    registered_clients = cur.fetchall()
    conn.close()

    admin_notifs_html = ""
    if admin_notifs:
        admin_items_html = ""
        for n in admin_notifs:
            admin_items_html += f'<div class="client-notif-box client-notif-accepted" id="admin-notif-{n["id"]}"><span>🔔 {n["body"]}</span><button type="button" onclick="dismissAdminNotif({n["id"]})" style="float:right;background:none;border:none;font-size:1rem;font-weight:900;cursor:pointer;color:inherit;">✕</button></div>'
        admin_notifs_html = f"""
        <div class="card-3d">
          <h2>🔔 Njoftimet e Adminit (Rezervimet e Reja)</h2>
          {admin_items_html}
        </div>
        """

    clients_cards_html = ""
    for cl in registered_clients:
        ident = cl["instagram"] or cl["email"] or f"Kliente #{cl['id']}"
        is_blk = cl["is_blocked"] == 1
        needs_rst = cl["password_reset_required"] == 1
        status_badges = []
        if is_blk:
            status_badges.append('<span class="badge b-blk">Bllokuar</span>')
        else:
            status_badges.append('<span class="badge b-ok">Aktive</span>')
        if needs_rst:
            status_badges.append('<span class="badge" style="background:#fef3c7;color:#92400e;">Reset Password aktiv</span>')

        blk_btn_label = "Zhblloko" if is_blk else "Blloko"
        blk_btn_cls = "btn-seen" if is_blk else "btn-del"

        clients_cards_html += f"""
        <div class="client-item-card">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:6px;">
            <b>{ident}</b>
            <div>{" ".join(status_badges)}</div>
          </div>
          <div style="font-size:0.84rem; color:var(--muted); margin-top:3px;">
            📞 {cl["phone"]} • <i>{cl["auth_method"]}</i>
          </div>
          <div style="display:flex; flex-wrap:wrap; gap:6px; margin-top:8px;">
            <form method="POST" style="display:inline;">
              <input type="hidden" name="action" value="reset_client_password">
              <input type="hidden" name="client_id" value="{cl['id']}">
              <input type="hidden" name="show_clients" value="1">
              <button type="submit" class="btn-sm-3d btn-reset">🔑 Reset Password</button>
            </form>
            <form method="POST" style="display:inline;">
              <input type="hidden" name="action" value="toggle_block_client">
              <input type="hidden" name="client_id" value="{cl['id']}">
              <input type="hidden" name="show_clients" value="1">
              <button type="submit" class="btn-sm-3d {blk_btn_cls}">🚫 {blk_btn_label}</button>
            </form>
            <form method="POST" style="display:inline;">
              <input type="hidden" name="action" value="delete_client">
              <input type="hidden" name="client_id" value="{cl['id']}">
              <input type="hidden" name="show_clients" value="1">
              <button type="submit" class="btn-sm-3d btn-del">🗑️ Fshi</button>
            </form>
          </div>
        </div>
        """

    clients_section_html = f"""
    <div class="card-3d" id="registeredClientsBox">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
        <h2 style="margin:0;">👥 Klientët e Regjistruar ({len(registered_clients)})</h2>
        <button type="button" id="toggleClientsBtn" onclick="toggleClientsSection()" class="btn-3d btn-vishnje-3d" style="padding:7px 11px; font-size:0.82rem;">
          {'Mbyll Listën' if show_clients_panel else 'Shiko Klientët'}
        </button>
      </div>
      <div id="clientsListContainer" style="display:{'block' if show_clients_panel else 'none'}; margin-top:10px;">
        {clients_cards_html if clients_cards_html else '<p style="font-size:0.88rem;color:var(--muted);">Nuk ka kliente të regjistruar ende.</p>'}
      </div>
    </div>
    """

    new_bookings_section = ""
    if new_appts:
        cards = ""
        for na in new_appts:
            notes_line = f"<div style='font-size:0.84rem; color:#881337; margin-top:4px;'>📝 <i>{na['notes']}</i></div>" if na["notes"] else ""
            contact_part = na["client_instagram"] or na["client_email"] or ""
            ig_line = f"<div style='font-size:0.86rem; color:#881337; margin-top:2px;'>🔗 <b>{contact_part}</b></div>" if contact_part else ""
            cards += f"""
            <div class="new-booking-card">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                <span class="badge-new-live">🔔 REZERVIM I RI</span>
                <span style="font-size:0.86rem; font-weight:800; color:#9f1239;">{na['appt_date']} • {na['start_time']} - {na['end_time']}</span>
              </div>
              <div style="font-size:1.02rem; font-weight:800; color:#4c0519;">{na['client_name']}</div>
              <div style="font-size:0.9rem; font-weight:700; color:#881337; margin-top:2px;">
                📞 <a href="tel:{na['client_phone']}" style="color:#be123c; text-decoration:underline;">{na['client_phone']}</a>
              </div>
              {ig_line}
              <div style="font-size:0.88rem; color:#261217; margin-top:4px;">
                💅 <b>[{na['category'] or '-'}] {na['service_name'] or '-'}</b> ({na['price'] or 0:.0f}€)
              </div>
              {notes_line}
              <div style="display:flex; gap:6px; margin-top:10px;">
                <form method="POST" style="flex:1;">
                  <input type="hidden" name="action" value="mark_seen">
                  <input type="hidden" name="appt_id" value="{na['id']}">
                  <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; padding:7px; font-size:0.84rem;">
                    ✓ Konfirmo Terminin
                  </button>
                </form>
                <form method="POST">
                  <input type="hidden" name="action" value="set_status">
                  <input type="hidden" name="appt_id" value="{na['id']}">
                  <input type="hidden" name="status" value="Anuluar">
                  <button type="submit" class="btn-sm-3d btn-del" style="padding:8px 10px;">Anulo</button>
                </form>
              </div>
            </div>
            """

        new_bookings_section = f"""
        <div class="card-3d" style="border: 2px solid #e11d48; background: #fff8f9;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <h2 style="margin:0; color:#be123c;">🚨 Rezervime të Reja ({len(new_appts)})</h2>
            <form method="POST">
              <input type="hidden" name="action" value="mark_all_seen">
              <button type="submit" class="btn-sm-3d btn-seen">Konfirmo të gjitha</button>
            </form>
          </div>
          {cards}
        </div>
        """

    rows_html = ""
    for a in appts:
        is_new = a["is_new"] == 1 and a["status"] == "Konfirmuar"
        row_cls = "row-is-new" if is_new else ""
        new_tag = '<span class="badge-new-live" style="font-size:0.68rem; padding:1px 6px; margin-bottom:3px;">I RI</span><br>' if is_new else ""
        badge_cls = "b-ok" if a["status"] == "Konfirmuar" else ("b-blk" if a["status"] == "Bllokuar" else "b-can")
        contact_val = a["client_instagram"] or a["client_email"] or ""
        ig_sub = f"<br><span style='font-size:0.76rem;color:#72091d;'>{contact_val}</span>" if contact_val else ""
        rows_html += f"""
        <tr class="{row_cls}">
          <td>{new_tag}<b>{a["appt_date"]}</b><br>{a["start_time"]}-{a["end_time"]}</td>
          <td><b>{a["client_name"]}</b><br><a href="tel:{a["client_phone"]}" style="color:#6e4e57; text-decoration:none;">{a["client_phone"]}</a>{ig_sub}</td>
          <td><span class="svc-tag">{a["category"] or "-"}</span><br><b>{a["service_name"] or "-"}</b></td>
          <td><span class="badge {badge_cls}">{a["status"]}</span></td>
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
              <button class="btn-sm-3d" type="submit">Anulo</button>
            </form>
            <form method="POST" style="display:inline;">
              <input type="hidden" name="action" value="delete_appt">
              <input type="hidden" name="appt_id" value="{a["id"]}">
              <button class="btn-sm-3d btn-del" type="submit">Fshi</button>
            </form>
          </td>
        </tr>
        """

    editable_services_html = ""
    for s in services:
        sel_thonj = "selected" if s["category"] == "Thonj" else ""
        sel_qerp = "selected" if s["category"] == "Qerpikë" else ""
        sel_vet = "selected" if s["category"] == "Vetulla" else ""
        sel_komb = "selected" if s["category"] == "Kombinim" else ""
        editable_services_html += f"""
        <div class="edit-svc-box">
          <form method="POST">
            <input type="hidden" name="action" value="update_service">
            <input type="hidden" name="svc_id" value="{s['id']}">
            <div class="row-2">
              <div>
                <label>Kategoria</label>
                <select name="s_cat">
                  <option value="Thonj" {sel_thonj}>Thonj</option>
                  <option value="Qerpikë" {sel_qerp}>Qerpikë</option>
                  <option value="Vetulla" {sel_vet}>Vetulla</option>
                  <option value="Kombinim" {sel_komb}>Kombinim</option>
                </select>
              </div>
              <div>
                <label>Çmimi (€)</label>
                <input type="number" name="s_price" value="{s['price']:.2f}" min="0" step="0.5" required>
              </div>
            </div>
            <label>Emri / Teksti i Artikullit</label>
            <input type="text" name="s_name" value="{s['name']}" required>
            <label>Kohëzgjatja (minuta)</label>
            <input type="number" name="s_dur" value="{s['duration_min']}" min="15" step="15" required>
            <label>Përshkrimi</label>
            <input type="text" name="s_desc" value="{s['description'] or ''}">
            <div style="display:flex; gap:8px; margin-top:10px;">
              <button type="submit" class="btn-3d btn-vishnje-3d" style="flex:1; padding:8px 12px; font-size:0.88rem;">
                💾 Ruaj
              </button>
            </div>
          </form>
          <form method="POST" style="margin-top:6px;">
            <input type="hidden" name="action" value="delete_service">
            <input type="hidden" name="svc_id" value="{s['id']}">
            <button type="submit" class="btn-sm-3d btn-del" style="width:100%;">🗑️ Hiq Artikullin</button>
          </form>
        </div>
        """

    alert_html = ""
    if feedback_msg:
        cls = "alert-ok" if feedback_type == "ok" else "alert-err"
        alert_html = f'<div class="alert {cls}">{feedback_msg}</div>'

    max_existing_id = max([a["id"] for a in appts], default=0)

    inner = f"""
      <div id="liveToastBanner" class="live-toast-banner">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <b style="font-size:1.02rem;">🔔 REZERVIM I RI SAPO ERDH!</b>
          <button type="button" onclick="document.getElementById('liveToastBanner').style.display='none'" style="background:none; border:none; color:#fff; font-weight:900; font-size:1.15rem; cursor:pointer;">✕</button>
        </div>
        <div id="liveToastBody" style="font-size:0.88rem; margin-top:6px; line-height:1.4;"></div>
        <button type="button" onclick="window.location.reload()" style="margin-top:9px; width:100%; background:#fff; color:#be123c; border:none; border-radius:9px; padding:7px; font-weight:800; cursor:pointer;">
          🔄 Rifresko Listën Tani
        </button>
      </div>

      <header>
        <div class="header-inner">
          <div>
            <h1 class="brand-title">GRANDE STUDIO</h1>
            <p class="brand-sub">{acc['email']}</p>
          </div>
          <div class="header-mid">
            {ig_nav_html}
          </div>
          <div class="header-actions">
            <a href="{url_for('index')}" class="btn-3d btn-ghost-3d">← Klienti</a>
            <a href="{url_for('admin_reset_password')}" class="btn-3d btn-ghost-3d">🔑 Reset password</a>
            <a href="{url_for('admin_logout')}" class="btn-3d btn-ghost-3d">Dil</a>
          </div>
        </div>
      </header>

      <div class="container">
        {alert_html}

        {clients_section_html}

        {admin_notifs_html}

        {new_bookings_section}

        <div class="card-3d">
          <h2>Terminet &amp; Bllokimet</h2>
          <div style="overflow-x:auto;">
            <table>
              <thead>
                <tr>
                  <th>Data/Ora</th>
                  <th>Klienti</th>
                  <th>Artikulli</th>
                  <th>Statusi</th>
                  <th>Veprime</th>
                </tr>
              </thead>
              <tbody>
                {rows_html if rows_html else '<tr><td colspan="5">Nuk ka termine ende.</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>

        <div class="card-3d">
          <h2>➕ Shto Artikull të Ri (Thonj, Qerpikë, Vetulla, Kombinim)</h2>
          <form method="POST">
            <input type="hidden" name="action" value="add_service">
            <div class="row-2">
              <div>
                <label>Kategoria</label>
                <select name="s_cat">
                  <option value="Thonj">Thonj</option>
                  <option value="Qerpikë">Qerpikë</option>
                  <option value="Vetulla">Vetulla</option>
                  <option value="Kombinim">Kombinim</option>
                </select>
              </div>
              <div>
                <label>Çmimi (€)</label>
                <input type="number" name="s_price" value="25" min="0" step="0.5" required>
              </div>
            </div>
            <label>Emri i artikullit</label>
            <input type="text" name="s_name" placeholder="p.sh. Laminim Vetullash + Xhel" required>
            <label>Kohëzgjatja (minuta)</label>
            <input type="number" name="s_dur" value="60" min="15" step="15" required>
            <label>Përshkrim i shkurtër</label>
            <input type="text" name="s_desc" placeholder="p.sh. Rezultat natyral dhe afatgjatë">
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">
              💾 Shto dhe Ruaj Artikullin
            </button>
          </form>
        </div>

        <div class="card-3d">
          <h2>✏️ Ndrysho ose Hiq Artikujt Ekzistues</h2>
          {editable_services_html if editable_services_html else '<p style="font-size:0.9rem;color:var(--muted);">Nuk ka artikuj aktualisht. Shtoni një artikull më lart.</p>'}
        </div>

        <div class="card-3d">
          <h2>Blloko Orar / Pushim</h2>
          <form method="POST">
            <input type="hidden" name="action" value="block_slot">
            <label>Data</label>
            <input type="date" name="block_date" value="{today_str}" required>
            <label>Arsyeja</label>
            <input type="text" name="block_reason" placeholder="p.sh. Pushim / Trajnim">
            <label>Nga ora</label>
            <input type="time" name="block_start" value="12:00" required>
            <label>Deri në orë</label>
            <input type="time" name="block_end" value="13:00" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">
              💾 Ruaj Bllokimin
            </button>
          </form>
        </div>

        <div class="card-3d">
          <h2>Ndrysho Fjalëkalimin e Adminit</h2>
          <form method="POST">
            <input type="hidden" name="action" value="change_password">
            <label for="current_password">Fjalëkalimi Aktual</label>
            <input type="password" name="current_password" id="current_password" required>

            <label for="new_password">Fjalëkalimi i Ri</label>
            <input type="password" name="new_password" id="new_password" required>

            <label for="confirm_password">Konfirmo Fjalëkalimin e Ri</label>
            <input type="password" name="confirm_password" id="confirm_password" required>

            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">
              💾 Ruaj Fjalëkalimin
            </button>
          </form>
        </div>
      </div>

      <script>
        try {{
          localStorage.setItem('grande_admin_persistent_token', '{active_admin_token}');
        }} catch (e) {{}}

        let lastKnownMaxId = {max_existing_id};

        function toggleClientsSection() {{
          const box = document.getElementById('clientsListContainer');
          const btn = document.getElementById('toggleClientsBtn');
          if (!box) return;
          const isHidden = box.style.display === 'none' || box.style.display === '';
          box.style.display = isHidden ? 'block' : 'none';
          if (btn) btn.textContent = isHidden ? 'Mbyll Listën' : 'Shiko Klientët';
        }}

        if ('serviceWorker' in navigator) {{
          navigator.serviceWorker.addEventListener('message', (event) => {{
            if (event.data && event.data.type === 'PLAY_CHIME') {{
              playNotificationChime();
            }}
          }});
        }}

        async function autoRegisterAdminPush() {{
          if ('Notification' in window && Notification.permission !== 'granted') {{
            try {{ await Notification.requestPermission(); }} catch (e) {{}}
          }}
          if ('serviceWorker' in navigator && 'PushManager' in window) {{
            try {{
              const reg = await navigator.serviceWorker.ready;
              const keyRes = await fetch('/api/push/public-key');
              const keyData = await keyRes.json();
              if (keyData.publicKey) {{
                let sub = await reg.pushManager.getSubscription();
                if (!sub) {{
                  sub = await reg.pushManager.subscribe({{
                    userVisibleOnly: true,
                    applicationServerKey: urlBase64ToUint8Array(keyData.publicKey)
                  }});
                }}
                await fetch('/api/push/subscribe', {{
                  method: 'POST',
                  headers: {{ 'Content-Type': 'application/json' }},
                  body: JSON.stringify(sub)
                }});
              }}
            }} catch (e) {{}}
          }}
        }}

        window.addEventListener('load', autoRegisterAdminPush);

        async function dismissAdminNotif(id) {{
          const el = document.getElementById('admin-notif-' + id);
          if (el) {{
            el.style.transform = 'translateX(100%)';
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 250);
          }}
          await fetch('/api/admin/notifications/read', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ id: id }})
          }});
          checkNewBookings();
        }}

        async function checkNewBookings() {{
          try {{
            const res = await fetch('/api/admin/new-bookings');
            if (!res.ok) return;
            const data = await res.json();
            if (data.bookings && data.bookings.length > 0) {{
              const newest = data.bookings[0];
              if (newest.id > lastKnownMaxId) {{
                lastKnownMaxId = newest.id;
                playNotificationChime();
                if (navigator.vibrate) navigator.vibrate([400, 150, 400]);

                const toast = document.getElementById('liveToastBanner');
                const toastBody = document.getElementById('liveToastBody');
                toastBody.innerHTML = `<b>${{newest.client_name}}</b> (${{newest.client_phone}})<br>💅 ${{newest.service_name}} — 📅 ${{newest.appt_date}} në <b>${{newest.start_time}}</b>`;
                toast.style.display = 'block';
              }}
            }}
          }} catch (e) {{}}
        }}

        setInterval(checkNewBookings, 4000);

        document.addEventListener('visibilitychange', () => {{
          if (!document.hidden) {{
            checkNewBookings();
          }}
        }});
      </script>
    """
    html_out = render_iphone_shell("Paneli i Adminit — Grande Studio", inner)
    resp = Response(html_out, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.set_cookie(
        ADMIN_REMEMBER_COOKIE,
        active_admin_token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )
    return resp


if __name__ == "__main__":
    init_db()
    prepare_clean_nails_asset()
    prepare_ig_avatar_asset()
    prepare_app_icons()
    get_vapid_keys()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)