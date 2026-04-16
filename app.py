import json
import os
import secrets
import sqlite3
import textwrap
import time
from datetime import datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import parse, request
import hashlib
import html
from urllib.error import HTTPError, URLError


BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "mindtrack.db"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
SESSION_COOKIE = "mindtrack_session"

MOOD_OPTIONS = ["happy", "neutral", "stressed", "sad"]
MOOD_SUGGESTIONS = {
    "stressed": [
        "Try a 60-second breathing exercise before your next task.",
        "Take a short break and reset with a glass of water.",
    ],
    "sad": [
        "Write down one thing weighing on you in a journal.",
        "Reach out to someone you trust for a quick check-in.",
    ],
    "neutral": [
        "Pick one priority for the next hour and keep it simple.",
        "Use a 25-minute focus block to build momentum.",
    ],
    "happy": [
        "Notice what is going well today and give yourself credit.",
        "Use this energy to finish one meaningful task.",
    ],
}
AI_RUNTIME_STATUS = {
    "state": "local",
    "message": "Free local support mode: using built-in supportive responses.",
}


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return f"{salt}${digest.hex()}"


def verify_password(password, stored_value):
    salt, _hash = stored_value.split("$", 1)
    return hash_password(password, salt) == stored_value


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS mood_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                entry_date TEXT NOT NULL,
                mood TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_message TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
    conn.close()


def read_template(name):
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def render_template(name, **context):
    template = read_template(name)
    merged = {key: str(value) for key, value in context.items()}
    return template.format(**merged).encode("utf-8")


def esc(value):
    return html.escape(value or "", quote=True)


def format_mood_cards(entries):
    if not entries:
        return '<p class="empty-state">No mood check-ins yet. Start with today.</p>'

    items = []
    for entry in entries:
        note = f'<p class="mood-note">{esc(entry["note"])}</p>' if entry["note"] else ""
        items.append(
            f"""
            <li class="history-item">
              <div>
                <strong>{esc(entry["entry_date"])}</strong>
                <span class="pill pill-{esc(entry["mood"])}">{esc(entry["mood"]).title()}</span>
              </div>
              {note}
            </li>
            """
        )
    return "\n".join(items)


def format_chart(entries):
    if not entries:
        return '<p class="empty-state">Your mood pattern will appear here after a few check-ins.</p>'

    mapping = {"sad": 25, "stressed": 45, "neutral": 70, "happy": 100}
    bars = []
    for entry in entries[:7]:
        bars.append(
            f"""
            <div class="chart-bar-wrap">
              <div class="chart-bar pill-{esc(entry["mood"])}" style="height:{mapping.get(entry["mood"], 40)}%">
                <span>{esc(entry["mood"]).title()}</span>
              </div>
              <small>{esc(entry["entry_date"][5:])}</small>
            </div>
            """
        )
    return "\n".join(bars)


def format_suggestions(mood):
    if not mood or mood not in MOOD_SUGGESTIONS:
        return '<p class="empty-state">Select a mood to get a gentle suggestion for today.</p>'
    return "\n".join(f"<li>{esc(item)}</li>" for item in MOOD_SUGGESTIONS[mood])


def format_chat_messages(messages):
    if not messages:
        return '<p class="empty-state">Start a conversation when you want a calm check-in or a small next step.</p>'

    blocks = []
    for msg in messages:
        blocks.append(
            f"""
            <article class="chat-pair">
              <div class="chat-bubble user-bubble">
                <strong>You</strong>
                <p>{esc(msg["user_message"])}</p>
              </div>
              <div class="chat-bubble ai-bubble">
                <strong>MindTrack AI</strong>
                <p>{esc(msg["ai_response"])}</p>
              </div>
              <small>{esc(msg["created_at"].replace("T", " "))}</small>
            </article>
            """
        )
    return "\n".join(blocks)


def get_ai_status():
    return AI_RUNTIME_STATUS.copy()


def local_support_reply(message):
    text = message.strip().lower()

    support_map = [
        (
            ["stress", "stressed", "overwhelmed", "pressure", "anxious", "anxiety"],
            "That sounds like a lot to carry. Try pausing for one slow breath, then choose the smallest next task so the pressure feels more manageable.",
        ),
        (
            ["sad", "down", "upset", "cry", "crying", "empty", "hopeless"],
            "I’m sorry today feels heavy. It may help to write one honest sentence about what hurts, then reach out to one person you trust if that feels possible.",
        ),
        (
            ["exam", "test", "final", "deadline", "assignment", "homework", "study"],
            "Exams can make everything feel urgent. Pick just one subject or one question to start with, and give yourself a short focused study block instead of trying to solve the whole day at once.",
        ),
        (
            ["tired", "exhausted", "burnout", "burned out", "drained", "no energy"],
            "It sounds like your energy is low. Try a brief reset first: water, stretch, and one small task only, because doing less can be the most helpful next step right now.",
        ),
        (
            ["alone", "lonely", "isolated", "nobody", "no one"],
            "Feeling alone can be really tough. If you can, send one simple message to someone safe like 'Hey, can we talk later?' so you do not have to hold everything by yourself.",
        ),
        (
            ["sleep", "insomnia", "can't sleep", "cannot sleep"],
            "A busy mind at night can be exhausting. Try putting your phone down for a few minutes, dimming the lights, and focusing only on slow breathing instead of forcing sleep.",
        ),
        (
            ["motivation", "unmotivated", "procrastinating", "procrastination", "stuck"],
            "It’s okay if motivation feels low. Start with two minutes of action on the easiest part, because momentum usually comes after starting, not before.",
        ),
    ]

    for keywords, reply in support_map:
        if any(keyword in text for keyword in keywords):
            return reply

    if "help" in text or "what should i do" in text:
        return (
            "Let’s keep it small. Name the hardest part of today, then take one gentle step toward it, even if that step only takes two minutes."
        )

    return (
        "Thanks for checking in. Try naming what you need most right now, like rest, focus, or support, and then give yourself one small practical step in that direction."
    )


def gemini_request(model, message, api_key):
    payload = {
        "system_instruction": {
            "parts": [
                {
                    "text": textwrap.dedent(
                        """
                        You are MindTrack AI, a supportive emotional wellness assistant for students ages 15-22.
                        Be warm, non-judgmental, and practical. Keep replies short: 2-4 sentences.
                        Offer emotional support and simple coping ideas only.
                        Do not present medical, psychological, crisis, or diagnostic advice.
                        If a user sounds in immediate danger, encourage contacting local emergency services or a trusted person right away.
                        """
                    ).strip()
                }
            ]
        },
        "contents": [
            {
                "parts": [
                    {"text": message},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 180,
        },
    }
    req = request.Request(
        (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        ),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        candidates = data.get("candidates", [])
        if not candidates:
            return {"reply": None, "error": "Gemini returned no response candidates.", "retryable": False}
        parts = candidates[0].get("content", {}).get("parts", [])
        reply = "".join(part.get("text", "") for part in parts).strip()
        if not reply:
            return {"reply": None, "error": "Gemini returned an empty response.", "retryable": False}
        return {"reply": reply, "error": None, "retryable": False}
    except HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        return {
            "reply": None,
            "error": f"Gemini mode error {exc.code}: {error_body[:220] or exc.reason}.",
            "retryable": exc.code in {429, 500, 503},
        }
    except URLError as exc:
        return {"reply": None, "error": f"Gemini network error: {exc.reason}.", "retryable": True}
    except Exception as exc:
        return {"reply": None, "error": f"Gemini unexpected error: {str(exc)[:220]}.", "retryable": False}


def gemini_support_reply(message):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    primary_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    fallback_model = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-1.5-flash")
    models_to_try = [primary_model]
    if fallback_model and fallback_model != primary_model:
        models_to_try.append(fallback_model)

    last_error = "Gemini is unavailable right now."
    for index, model in enumerate(models_to_try):
        attempts = 2 if index == 0 else 1
        for attempt in range(attempts):
            result = gemini_request(model, message, api_key)
            if result["reply"]:
                AI_RUNTIME_STATUS["state"] = "connected"
                suffix = " after retry" if attempt > 0 else ""
                AI_RUNTIME_STATUS["message"] = f"Gemini mode: connected with {model}{suffix}."
                return result["reply"]

            last_error = result["error"] or last_error
            if result["retryable"] and attempt < attempts - 1:
                time.sleep(1.2)
                continue
            break

    AI_RUNTIME_STATUS["state"] = "error"
    if len(models_to_try) > 1:
        AI_RUNTIME_STATUS["message"] = (
            f"{last_error} Tried {primary_model} and fallback {fallback_model}."
        )
    else:
        AI_RUNTIME_STATUS["message"] = last_error
    return None


def assistant_reply(message):
    gemini_reply = gemini_support_reply(message)
    if gemini_reply:
        return gemini_reply

    if os.environ.get("GEMINI_API_KEY"):
        if AI_RUNTIME_STATUS.get("state") != "error":
            AI_RUNTIME_STATUS["state"] = "local"
            AI_RUNTIME_STATUS["message"] = (
                "Free local support mode: Gemini is unavailable right now, so built-in supportive responses are active."
            )
    else:
        AI_RUNTIME_STATUS["state"] = "local"
        AI_RUNTIME_STATUS["message"] = (
            "Free local support mode: using built-in supportive responses."
        )

    return local_support_reply(message)


class MindTrackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = parse.urlparse(self.path).path
        if path.startswith("/static/"):
            return self.serve_static(path)
        if path == "/":
            return self.route_home()
        if path == "/login":
            return self.route_login()
        if path == "/signup":
            return self.route_signup()
        if path == "/logout":
            return self.route_logout()
        self.send_error(404, "Page not found")

    def do_POST(self):
        path = parse.urlparse(self.path).path
        if path == "/signup":
            return self.handle_signup()
        if path == "/login":
            return self.handle_login()
        if path == "/mood":
            return self.handle_mood()
        if path == "/chat":
            return self.handle_chat()
        self.send_error(404, "Page not found")

    def parse_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        data = parse.parse_qs(raw)
        return {key: value[0] for key, value in data.items()}

    def get_current_user(self):
        raw_cookie = self.headers.get("Cookie")
        if not raw_cookie:
            return None

        jar = cookies.SimpleCookie()
        jar.load(raw_cookie)
        session = jar.get(SESSION_COOKIE)
        if not session:
            return None

        conn = get_db()
        user = conn.execute(
            """
            SELECT users.id, users.email
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ?
            """,
            (session.value,),
        ).fetchone()
        conn.close()
        return user

    def send_html(self, body, status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location, headers=None):
        extra_headers = {"Location": location}
        if headers:
            extra_headers.update(headers)
        self.send_response(302)
        for key, value in extra_headers.items():
            self.send_header(key, value)
        self.end_headers()

    def serve_static(self, path):
        file_path = BASE_DIR / path.lstrip("/")
        if not file_path.exists():
            return self.send_error(404, "File not found")

        content_type = "text/plain"
        if path.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif path.endswith(".js"):
            content_type = "application/javascript; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def route_home(self):
        user = self.get_current_user()
        if not user:
            return self.redirect("/login")

        conn = get_db()
        entries = conn.execute(
            """
            SELECT entry_date, mood, note
            FROM mood_entries
            WHERE user_id = ?
            ORDER BY entry_date DESC, id DESC
            LIMIT 10
            """,
            (user["id"],),
        ).fetchall()
        latest = entries[0]["mood"] if entries else ""
        ai_status = get_ai_status()
        messages = conn.execute(
            """
            SELECT user_message, ai_response, created_at
            FROM chat_messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 8
            """,
            (user["id"],),
        ).fetchall()
        conn.close()

        body = render_template(
            "dashboard.html",
            email=esc(user["email"]),
            mood_history=format_mood_cards(entries),
            mood_chart=format_chart(list(reversed(entries))),
            suggestions=format_suggestions(latest),
            chat_messages=format_chat_messages(reversed(messages)),
            selected_happy="checked" if latest == "happy" else "",
            selected_neutral="checked" if latest == "neutral" else "",
            selected_stressed="checked" if latest == "stressed" else "",
            selected_sad="checked" if latest == "sad" else "",
            today=datetime.now().strftime("%B %d, %Y"),
            openai_status_text=esc(ai_status["message"]),
            openai_status_class=esc(ai_status["state"]),
        )
        return self.send_html(body)

    def route_login(self, error=""):
        if self.get_current_user():
            return self.redirect("/")
        body = render_template("login.html", error=self.render_flash(error), title="Welcome back")
        return self.send_html(body)

    def route_signup(self, error=""):
        if self.get_current_user():
            return self.redirect("/")
        body = render_template("signup.html", error=self.render_flash(error), title="Create your account")
        return self.send_html(body)

    def route_logout(self):
        raw_cookie = cookies.SimpleCookie()
        raw_cookie[SESSION_COOKIE] = ""
        raw_cookie[SESSION_COOKIE]["path"] = "/"
        raw_cookie[SESSION_COOKIE]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        return self.redirect("/login", headers={"Set-Cookie": raw_cookie.output(header="").strip()})

    def render_flash(self, message):
        if not message:
            return ""
        return f'<p class="form-error">{esc(message)}</p>'

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        conn = get_db()
        with conn:
            conn.execute(
                "INSERT INTO sessions (user_id, token, created_at) VALUES (?, ?, ?)",
                (user_id, token, now_iso()),
            )
        conn.close()
        jar = cookies.SimpleCookie()
        jar[SESSION_COOKIE] = token
        jar[SESSION_COOKIE]["path"] = "/"
        jar[SESSION_COOKIE]["httponly"] = True
        jar[SESSION_COOKIE]["samesite"] = "Lax"
        return jar.output(header="").strip()

    def handle_signup(self):
        form = self.parse_form()
        email = form.get("email", "").strip().lower()
        password = form.get("password", "")
        if not email or "@" not in email or len(password) < 6:
            return self.route_signup("Use a valid email and a password with at least 6 characters.")

        conn = get_db()
        try:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                    (email, hash_password(password), now_iso()),
                )
            user_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            conn.close()
            return self.route_signup("That email is already registered.")
        conn.close()
        return self.redirect("/", headers={"Set-Cookie": self.create_session(user_id)})

    def handle_login(self):
        form = self.parse_form()
        email = form.get("email", "").strip().lower()
        password = form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if not user or not verify_password(password, user["password_hash"]):
            return self.route_login("Email or password did not match.")
        return self.redirect("/", headers={"Set-Cookie": self.create_session(user["id"])})

    def handle_mood(self):
        user = self.get_current_user()
        if not user:
            return self.redirect("/login")

        form = self.parse_form()
        mood = form.get("mood", "").strip().lower()
        note = form.get("note", "").strip()
        if mood not in MOOD_OPTIONS:
            return self.redirect("/")

        today = datetime.now().strftime("%Y-%m-%d")
        conn = get_db()
        with conn:
            conn.execute(
                """
                INSERT INTO mood_entries (user_id, entry_date, mood, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user["id"], today, mood, note[:240], now_iso()),
            )
        conn.close()
        return self.redirect("/")

    def handle_chat(self):
        user = self.get_current_user()
        if not user:
            return self.redirect("/login")

        form = self.parse_form()
        message = form.get("message", "").strip()
        if not message:
            return self.redirect("/")

        ai_reply = assistant_reply(message)
        conn = get_db()
        with conn:
            conn.execute(
                """
                INSERT INTO chat_messages (user_id, user_message, ai_response, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user["id"], message[:1000], ai_reply[:2000], now_iso()),
            )
        conn.close()
        return self.redirect("/")


def main():
    init_db()
    if os.environ.get("GEMINI_API_KEY"):
        AI_RUNTIME_STATUS["state"] = "configured"
        AI_RUNTIME_STATUS["message"] = (
            f'Gemini mode: configured and ready to test with {os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")}.'
        )
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), MindTrackHandler)
    print(f"MindTrack AI running on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
