import os
import json
import sqlite3
import datetime
import base64
import hashlib
import numpy as np
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from werkzeug.utils import secure_filename
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "alzheimer_support_2024_secret")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

# ─── Configuration ───────────────────────────────────────────
DATASET_DIR = os.path.join(os.path.dirname(__file__), "dataset")
TRAINER_DIR = os.path.join(os.path.dirname(__file__), "trainer")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

for d in [DATASET_DIR, TRAINER_DIR, DATA_DIR]:
    os.makedirs(d, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "alzheimer.db")

# ─── Database Setup ──────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS known_people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            relationship TEXT DEFAULT '',
            contact TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            time TEXT NOT NULL,
            type TEXT DEFAULT 'general',
            status TEXT DEFAULT 'pending',
            date TEXT DEFAULT (date('now')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT DEFAULT '',
            category TEXT DEFAULT 'general',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS recognition_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            person_name TEXT NOT NULL,
            confidence REAL DEFAULT 0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS chatbot_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            total_tasks INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            missed INTEGER DEFAULT 0,
            adherence REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, date)
        );

        CREATE INDEX IF NOT EXISTS idx_known_people_user_id ON known_people(user_id);
        CREATE INDEX IF NOT EXISTS idx_reminders_user_date ON reminders(user_id, date);
        CREATE INDEX IF NOT EXISTS idx_activity_logs_user_time ON activity_logs(user_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_recognition_logs_user_time ON recognition_logs(user_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_chatbot_memory_user_time ON chatbot_memory(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_daily_stats_user_date ON daily_stats(user_id, date);
    """)
    conn.commit()
    conn.close()

def migrate_db():
    """Add user_id column to existing tables if missing (for existing databases)."""
    conn = get_db()
    tables_needing_user_id = [
        'known_people', 'reminders', 'activity_logs',
        'recognition_logs', 'chatbot_memory', 'daily_stats'
    ]
    for table in tables_needing_user_id:
        try:
            conn.execute(f"SELECT user_id FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER DEFAULT 0")
            conn.commit()
    # Ensure users table exists
    try:
        conn.execute("SELECT id FROM users LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    conn.close()

init_db()
migrate_db()

# ─── Auth Helpers ────────────────────────────────────────────
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_current_user_id():
    return session.get("user_id")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user_id():
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"success": False, "error": "Login required"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

# ─── Helper Functions ────────────────────────────────────────
def log_activity(action, details="", category="general"):
    uid = get_current_user_id()
    if not uid:
        return
    conn = get_db()
    conn.execute("INSERT INTO activity_logs (user_id, action, details, category) VALUES (?, ?, ?, ?)",
                 (uid, action, details, category))
    conn.commit()
    conn.close()

def get_user_dataset_dir(user_id):
    d = os.path.join(DATASET_DIR, f"user_{user_id}")
    os.makedirs(d, exist_ok=True)
    return d

def get_user_trainer_dir(user_id):
    d = os.path.join(TRAINER_DIR, f"user_{user_id}")
    os.makedirs(d, exist_ok=True)
    return d

def get_labels_map(user_id):
    path = os.path.join(get_user_trainer_dir(user_id), "labels.json")
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return {}

def save_labels_map(labels, user_id):
    path = os.path.join(get_user_trainer_dir(user_id), "labels.json")
    with open(path, 'w') as f:
        json.dump(labels, f)

# ─── Routes: Auth ─────────────────────────────────────────────
@app.route("/login")
def login_page():
    if get_current_user_id():
        return redirect(url_for("role_select"))
    return render_template("login.html")

@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.json
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    display_name = data.get("display_name", "").strip() or username

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password required"})
    if len(password) < 4:
        return jsonify({"success": False, "error": "Password must be at least 4 characters"})

    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        conn.close()
        return jsonify({"success": False, "error": "Username already taken"})

    conn.execute("INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?)",
                 (username, hash_password(password), display_name))
    conn.commit()
    user = conn.execute("SELECT id, display_name FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()

    session["user_id"] = user["id"]
    session["username"] = username
    session["display_name"] = user["display_name"]
    return jsonify({"success": True, "redirect": url_for("role_select")})

@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.json
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()

    if not user or user["password_hash"] != hash_password(password):
        return jsonify({"success": False, "error": "Invalid username or password"})

    session["user_id"] = user["id"]
    session["username"] = username
    session["display_name"] = user["display_name"]
    return jsonify({"success": True, "redirect": url_for("role_select")})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ─── Routes: Landing & Role Selection ────────────────────────
@app.route("/")
def index():
    return render_template("index.html", display_name=session.get("display_name", ""))

@app.route("/role-select")
@login_required
def role_select():
    return render_template("role_select.html", display_name=session.get("display_name", ""))

@app.route("/patient")
@login_required
def patient_dashboard():
    uid = get_current_user_id()
    conn = get_db()
    today = datetime.date.today().isoformat()
    reminders = conn.execute(
        "SELECT * FROM reminders WHERE user_id = ? AND date = ? ORDER BY time", (uid, today,)
    ).fetchall()
    people = conn.execute("SELECT * FROM known_people WHERE user_id = ? ORDER BY name", (uid,)).fetchall()
    conn.close()
    return render_template("patient_dashboard.html", reminders=reminders, people=people,
                           display_name=session.get("display_name", ""))

@app.route("/caretaker")
@login_required
def caretaker_dashboard():
    uid = get_current_user_id()
    conn = get_db()
    today = datetime.date.today().isoformat()
    reminders = conn.execute(
        "SELECT * FROM reminders WHERE user_id = ? AND date = ? ORDER BY time", (uid, today,)
    ).fetchall()
    total = len(reminders)
    completed = sum(1 for r in reminders if r["status"] == "done")
    pending = sum(1 for r in reminders if r["status"] == "pending")
    missed = sum(1 for r in reminders if r["status"] == "missed")

    recent_logs = conn.execute(
        "SELECT * FROM activity_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20", (uid,)
    ).fetchall()

    recognition_logs = conn.execute(
        "SELECT * FROM recognition_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20", (uid,)
    ).fetchall()

    people = conn.execute("SELECT * FROM known_people WHERE user_id = ? ORDER BY name", (uid,)).fetchall()

    week_stats = conn.execute(
        "SELECT date, completed, total_tasks, missed, adherence FROM daily_stats WHERE user_id = ? ORDER BY date DESC LIMIT 7",
        (uid,)
    ).fetchall()

    conn.close()

    stats = {
        "total": total, "completed": completed,
        "pending": pending, "missed": missed,
        "adherence": round((completed / total * 100) if total > 0 else 0, 1)
    }

    return render_template("caretaker_dashboard.html",
        reminders=reminders, stats=stats, recent_logs=recent_logs,
        recognition_logs=recognition_logs, people=people, week_stats=week_stats,
        display_name=session.get("display_name", ""))

# ─── Routes: Known People Management ─────────────────────────
@app.route("/api/people", methods=["GET"])
@login_required
def get_people():
    uid = get_current_user_id()
    conn = get_db()
    people = conn.execute("SELECT * FROM known_people WHERE user_id = ? ORDER BY name", (uid,)).fetchall()
    conn.close()
    return jsonify([dict(p) for p in people])

@app.route("/api/people", methods=["POST"])
@login_required
def add_person():
    uid = get_current_user_id()
    data = request.json
    conn = get_db()
    conn.execute(
        "INSERT INTO known_people (user_id, name, relationship, contact, notes) VALUES (?, ?, ?, ?, ?)",
        (uid, data["name"], data.get("relationship", ""), data.get("contact", ""), data.get("notes", ""))
    )
    conn.commit()
    conn.close()
    log_activity("Person Added", f"Added {data['name']} ({data.get('relationship', '')})", "people")
    return jsonify({"success": True})

@app.route("/api/people/<int:pid>", methods=["DELETE"])
@login_required
def delete_person(pid):
    uid = get_current_user_id()
    conn = get_db()
    conn.execute("DELETE FROM known_people WHERE id = ? AND user_id = ?", (pid, uid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ─── Routes: Face Registration ───────────────────────────────
@app.route("/face-register")
@login_required
def face_register_page():
    return render_template("face_register.html")

@app.route("/api/face/register", methods=["POST"])
@login_required
def face_register():
    uid = get_current_user_id()
    data = request.json
    name = data.get("name", "").strip()
    images = data.get("images", [])

    if not name or len(images) < 5:
        return jsonify({"success": False, "error": "Need name and at least 5 images"})

    user_dataset = get_user_dataset_dir(uid)
    person_dir = os.path.join(user_dataset, name.replace(" ", "_"))
    os.makedirs(person_dir, exist_ok=True)

    for i, img_data in enumerate(images):
        img_bytes = base64.b64decode(img_data.split(",")[1] if "," in img_data else img_data)
        with open(os.path.join(person_dir, f"{i+1}.jpg"), "wb") as f:
            f.write(img_bytes)

    log_activity("Face Registered", f"Registered {name} with {len(images)} images", "face")
    return jsonify({"success": True, "message": f"Registered {name} with {len(images)} images"})

# ─── Routes: Face Training ───────────────────────────────────
@app.route("/face-train")
@login_required
def face_train_page():
    return render_template("face_train.html")

@app.route("/api/face/train", methods=["POST"])
@login_required
def face_train():
    uid = get_current_user_id()
    try:
        import cv2
    except ImportError:
        return jsonify({"success": False, "error": "OpenCV not installed. Run: pip install opencv-contrib-python"})

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    faces_list = []
    labels_list = []
    labels_map = {}
    label_id = 0

    user_dataset = get_user_dataset_dir(uid)
    if not os.path.exists(user_dataset):
        return jsonify({"success": False, "error": "No dataset directory found"})

    persons = [d for d in os.listdir(user_dataset) if os.path.isdir(os.path.join(user_dataset, d))]
    if not persons:
        return jsonify({"success": False, "error": "No registered faces found"})

    for person_name in persons:
        person_dir = os.path.join(user_dataset, person_name)
        labels_map[str(label_id)] = person_name.replace("_", " ")

        for img_file in os.listdir(person_dir):
            if img_file.lower().endswith(('.jpg', '.jpeg', '.png')):
                img_path = os.path.join(person_dir, img_file)
                img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue
                detected = face_cascade.detectMultiScale(img, 1.3, 5)
                for (x, y, w, h) in detected:
                    faces_list.append(img[y:y+h, x:x+w])
                    labels_list.append(label_id)
        label_id += 1

    if len(faces_list) == 0:
        return jsonify({"success": False, "error": "No faces detected in images"})

    user_trainer = get_user_trainer_dir(uid)
    recognizer.train(faces_list, np.array(labels_list))
    recognizer.write(os.path.join(user_trainer, "trainer.yml"))
    save_labels_map(labels_map, uid)

    log_activity("Model Trained", f"Trained on {len(faces_list)} face samples from {len(persons)} people", "face")
    return jsonify({"success": True, "message": f"Trained on {len(faces_list)} samples from {len(persons)} people"})

# ─── Routes: Face Recognition ────────────────────────────────
@app.route("/face-recognize")
@login_required
def face_recognize_page():
    return render_template("face_recognize.html")

@app.route("/api/face/recognize", methods=["POST"])
@login_required
def face_recognize():
    uid = get_current_user_id()
    try:
        import cv2
    except ImportError:
        return jsonify({"success": False, "error": "OpenCV not installed"})

    user_trainer = get_user_trainer_dir(uid)
    trainer_path = os.path.join(user_trainer, "trainer.yml")
    if not os.path.exists(trainer_path):
        return jsonify({"success": False, "error": "No trained model found. Please train first."})

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(trainer_path)
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    labels_map = get_labels_map(uid)

    data = request.json
    img_data = data.get("image", "")
    if not img_data:
        return jsonify({"success": False, "error": "No image provided"})

    img_bytes = base64.b64decode(img_data.split(",")[1] if "," in img_data else img_data)
    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"success": False, "error": "Invalid image"})

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    if len(faces) == 0:
        return jsonify({"success": True, "faces": []})

    results = []
    conn = get_db()
    for (x, y, w, h) in faces:
        roi_gray = gray[y:y+h, x:x+w]
        label_id, confidence = recognizer.predict(roi_gray)
        conf_pct = round(100 - confidence, 1)

        if confidence < 80:
            name = labels_map.get(str(label_id), "Unknown")
            person_info = conn.execute(
                "SELECT * FROM known_people WHERE user_id = ? AND LOWER(name) = LOWER(?)", (uid, name,)
            ).fetchone()

            relationship = dict(person_info).get("relationship", "") if person_info else ""

            recent = conn.execute(
                "SELECT * FROM recognition_logs WHERE user_id = ? AND person_name = ? AND timestamp > datetime('now', '-5 minutes')",
                (uid, name,)
            ).fetchone()
            if not recent:
                conn.execute("INSERT INTO recognition_logs (user_id, person_name, confidence) VALUES (?, ?, ?)",
                             (uid, name, conf_pct))
                conn.commit()

            results.append({
                "name": name, "confidence": conf_pct,
                "relationship": relationship,
                "known": True,
                "box": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
            })
        else:
            results.append({
                "name": "Unknown Person", "confidence": conf_pct,
                "relationship": "", "known": False,
                "box": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
            })

    conn.close()
    return jsonify({"success": True, "faces": results})

# ─── Routes: Reminders ───────────────────────────────────────
@app.route("/api/reminders", methods=["GET"])
@login_required
def get_reminders():
    uid = get_current_user_id()
    date = request.args.get("date", datetime.date.today().isoformat())
    conn = get_db()
    reminders = conn.execute(
        "SELECT * FROM reminders WHERE user_id = ? AND date = ? ORDER BY time", (uid, date,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in reminders])

@app.route("/api/reminders", methods=["POST"])
@login_required
def add_reminder():
    uid = get_current_user_id()
    data = request.json
    conn = get_db()
    conn.execute(
        "INSERT INTO reminders (user_id, title, time, type, date) VALUES (?, ?, ?, ?, ?)",
        (uid, data["title"], data["time"], data.get("type", "general"),
         data.get("date", datetime.date.today().isoformat()))
    )
    conn.commit()
    conn.close()
    log_activity("Reminder Added", f"{data['title']} at {data['time']}", "reminder")
    return jsonify({"success": True})

@app.route("/api/reminders/<int:rid>/complete", methods=["POST"])
@login_required
def complete_reminder(rid):
    uid = get_current_user_id()
    conn = get_db()
    conn.execute("UPDATE reminders SET status = 'done' WHERE id = ? AND user_id = ?", (rid, uid))
    conn.commit()
    r = conn.execute("SELECT * FROM reminders WHERE id = ? AND user_id = ?", (rid, uid)).fetchone()
    conn.close()
    if r:
        log_activity("Reminder Completed", f"{r['title']}", "reminder")
    return jsonify({"success": True})

@app.route("/api/reminders/<int:rid>", methods=["DELETE"])
@login_required
def delete_reminder(rid):
    uid = get_current_user_id()
    conn = get_db()
    conn.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (rid, uid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ─── Routes: Activity Logs ───────────────────────────────────
@app.route("/api/logs", methods=["GET"])
@login_required
def get_logs():
    uid = get_current_user_id()
    category = request.args.get("category", None)
    conn = get_db()
    if category:
        logs = conn.execute(
            "SELECT * FROM activity_logs WHERE user_id = ? AND category = ? ORDER BY timestamp DESC LIMIT 50",
            (uid, category,)
        ).fetchall()
    else:
        logs = conn.execute(
            "SELECT * FROM activity_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 50", (uid,)
        ).fetchall()
    conn.close()
    return jsonify([dict(l) for l in logs])

# ─── Routes: Analytics ───────────────────────────────────────
@app.route("/api/analytics", methods=["GET"])
@login_required
def get_analytics():
    uid = get_current_user_id()
    conn = get_db()
    today = datetime.date.today().isoformat()

    reminders = conn.execute("SELECT * FROM reminders WHERE user_id = ? AND date = ?", (uid, today,)).fetchall()
    total = len(reminders)
    completed = sum(1 for r in reminders if r["status"] == "done")
    missed = sum(1 for r in reminders if r["status"] == "missed")

    visitors = conn.execute(
        "SELECT person_name, COUNT(*) as count FROM recognition_logs WHERE user_id = ? GROUP BY person_name ORDER BY count DESC LIMIT 5",
        (uid,)
    ).fetchall()

    week_data = []
    for i in range(6, -1, -1):
        d = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
        r = conn.execute("SELECT * FROM reminders WHERE user_id = ? AND date = ?", (uid, d,)).fetchall()
        t = len(r)
        c = sum(1 for x in r if x["status"] == "done")
        week_data.append({"date": d, "total": t, "completed": c, "adherence": round(c/t*100, 1) if t > 0 else 0})

    conn.close()
    return jsonify({
        "today": {"total": total, "completed": completed, "missed": missed,
                  "adherence": round(completed/total*100, 1) if total > 0 else 0},
        "visitors": [dict(v) for v in visitors],
        "weekly": week_data
    })

# ─── AI Chatbot Configuration ─────────────────────────────────
import requests as http_requests

CHATBOT_CONFIG = {
    "provider": os.environ.get("CHATBOT_PROVIDER", "ollama"),
    "ollama_url": os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat"),
    "ollama_model": os.environ.get("OLLAMA_MODEL", "mistral"),
    "openai_url": os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions"),
    "openai_key": os.environ.get("OPENAI_API_KEY", ""),
    "openai_model": os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo"),
}

def build_chat_context(conn, user_id):
    now = datetime.datetime.now()
    context_parts = []
    context_parts.append(f"Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}")

    people = conn.execute("SELECT name, relationship, contact, notes FROM known_people WHERE user_id = ?", (user_id,)).fetchall()
    if people:
        people_info = []
        for p in people:
            info = f"- {p['name']} ({p['relationship']})"
            if p['notes']:
                info += f": {p['notes']}"
            people_info.append(info)
        context_parts.append("People the patient knows:\n" + "\n".join(people_info))

    today = now.strftime("%Y-%m-%d")
    reminders = conn.execute(
        "SELECT title, time, type, status FROM reminders WHERE user_id = ? AND date = ? ORDER BY time", (user_id, today,)
    ).fetchall()
    if reminders:
        rem_info = []
        for r in reminders:
            status_str = "✅ Done" if r['status'] == 'done' else "⏳ Pending"
            rem_info.append(f"- {r['title']} at {r['time']} ({r['type']}) - {status_str}")
        context_parts.append("Today's reminders:\n" + "\n".join(rem_info))

    memories = conn.execute(
        "SELECT key, value FROM chatbot_memory WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (user_id,)
    ).fetchall()
    if memories:
        mem_info = ["- " + m['value'] for m in memories]
        context_parts.append("Things the patient has told me before:\n" + "\n".join(mem_info))

    return "\n\n".join(context_parts)


def get_system_prompt(context):
    return f"""You are a warm, gentle, and supportive AI companion for an Alzheimer's patient. Your name is AlzCare AI Friend.

IMPORTANT RULES:
- Keep responses SHORT (2-4 sentences max), simple, and easy to understand
- Use a calm, warm, reassuring tone with occasional emojis (😊💛🌸)
- NEVER make up facts about the patient's family or life - ONLY use the information provided below
- If you don't know something, say so kindly and suggest asking their caretaker
- NEVER give medical advice or make medical claims
- If the patient seems scared, confused, or anxious, be extra reassuring and calming
- Help with daily reminders when asked
- If the patient tells you personal facts (like "my name is...", "I like..."), acknowledge warmly
- For date/time questions, use the current date/time provided
- Respond in simple English

PATIENT CONTEXT:
{context}

Remember: You are a friend, not a doctor. Be kind, patient, and supportive."""


def call_llm(user_message, context):
    system_prompt = get_system_prompt(context)
    provider = CHATBOT_CONFIG["provider"]

    try:
        if provider == "ollama":
            resp = http_requests.post(
                CHATBOT_CONFIG["ollama_url"],
                json={
                    "model": CHATBOT_CONFIG["ollama_model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message}
                    ],
                    "stream": False
                },
                timeout=30
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("message", {}).get("content", "").strip()
            return None

        elif provider == "openai":
            headers = {
                "Authorization": f"Bearer {CHATBOT_CONFIG['openai_key']}",
                "Content-Type": "application/json"
            }
            resp = http_requests.post(
                CHATBOT_CONFIG["openai_url"],
                headers=headers,
                json={
                    "model": CHATBOT_CONFIG["openai_model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message}
                    ],
                    "max_tokens": 200,
                    "temperature": 0.7
                },
                timeout=30
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            return None

        else:
            return None

    except Exception as e:
        print(f"[Chatbot LLM Error] {e}")
        return None


def fallback_chat(user_msg, conn, user_id):
    msg = user_msg.lower()

    memory_keywords = ["my ", "i am ", "i like ", "i love ", "i have "]
    for kw in memory_keywords:
        if msg.startswith(kw):
            conn.execute("INSERT INTO chatbot_memory (user_id, key, value) VALUES (?, ?, ?)",
                         (user_id, msg, user_msg))
            conn.commit()
            return f"I'll remember that for you! 💛 You told me: \"{user_msg}\""

    if any(w in msg for w in ["who is", "who's", "tell me about"]):
        people = conn.execute("SELECT * FROM known_people WHERE user_id = ?", (user_id,)).fetchall()
        for p in people:
            if p["name"].lower() in msg:
                response = f"😊 {p['name']} is your {p['relationship']}."
                if p['notes']:
                    response += f" {p['notes']}"
                return response
        return "I don't have information about that person yet. Your caretaker can add them! 💙"

    if any(w in msg for w in ["remind", "task", "what do i", "what should"]):
        today = datetime.date.today().isoformat()
        reminders = conn.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND date = ? AND status = 'pending' ORDER BY time LIMIT 3",
            (user_id, today,)
        ).fetchall()
        if reminders:
            tasks = ", ".join([f"{r['title']} at {r['time']}" for r in reminders])
            return f"Here are your upcoming tasks: {tasks} 😊"
        return "No pending tasks right now! Enjoy your day 🌸"

    if any(w in msg for w in ["hello", "hi", "hey", "good morning", "good evening"]):
        return "Hello! 😊 I'm here with you. How are you feeling today?"

    if any(w in msg for w in ["thank", "thanks"]):
        return "You're very welcome! 💙 I'm always here for you."

    if any(w in msg for w in ["scared", "afraid", "worried", "anxious", "confused"]):
        return "It's okay to feel that way. 💛 You are safe. Take a deep breath. I'm right here with you."

    if any(w in msg for w in ["what day", "what date", "today"]):
        now = datetime.datetime.now()
        return f"Today is {now.strftime('%A, %B %d, %Y')}. The time is {now.strftime('%I:%M %p')}. 📅"

    if any(w in msg for w in ["what time", "time now"]):
        now = datetime.datetime.now()
        return f"The time right now is {now.strftime('%I:%M %p')} ⏰"

    if any(w in msg for w in ["bye", "goodbye", "good night"]):
        return "Goodbye! 🌙 Take care and rest well. I'll be here whenever you need me. 💙"

    if any(w in msg for w in ["help", "what can you"]):
        return "I can help you with many things! 😊\n• Ask about your family or friends\n• Ask about your reminders\n• Just chat with me\n• Tell me things to remember\n• Ask what day or time it is"

    if any(w in msg for w in ["game", "play", "quiz", "memory game"]):
        import random
        games = [
            "🎨 What color is the sky on a clear day?",
            "🔤 What word comes after 'good'? (morning / car / purple)",
            "🤔 Which one is different? Apple, Banana, Chair, Mango",
        ]
        return f"Let's play! 🎮\n\n{random.choice(games)}\n\nTake your time! 💛"

    import random
    return random.choice([
        "That's interesting! Tell me more 😊",
        "I'm listening! 💛 Is there anything you'd like to know?",
        "Thank you for sharing! What else is on your mind? 🌸",
        "I'm here for you! Would you like to talk about your day? 😊",
    ])


# ─── Routes: Chatbot ─────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    uid = get_current_user_id()
    data = request.json
    user_msg = data.get("message", "").strip()

    if not user_msg:
        return jsonify({"response": "I didn't catch that. Could you say it again? 😊"})

    conn = get_db()

    msg_lower = user_msg.lower()
    memory_keywords = ["my ", "i am ", "i like ", "i love ", "i have "]
    for kw in memory_keywords:
        if msg_lower.startswith(kw):
            conn.execute("INSERT INTO chatbot_memory (user_id, key, value) VALUES (?, ?, ?)",
                         (uid, msg_lower, user_msg))
            conn.commit()
            break

    context = build_chat_context(conn, uid)
    response = call_llm(user_msg, context)

    if not response:
        response = fallback_chat(user_msg, conn, uid)

    conn.close()
    log_activity("Chat", f"User: {user_msg[:50]}", "chat")
    return jsonify({"response": response})


# ─── Routes: Pending Reminders for Voice Alerts ──────────────
@app.route("/api/reminders/pending-now", methods=["GET"])
@login_required
def get_pending_now():
    uid = get_current_user_id()
    conn = get_db()
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now()
    current_time = now.strftime("%H:%M")

    reminders = conn.execute(
        "SELECT * FROM reminders WHERE user_id = ? AND date = ? AND status = 'pending' AND time <= ? ORDER BY time",
        (uid, today, current_time)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in reminders])


# ─── Routes: Save Daily Stats ────────────────────────────────
@app.route("/api/stats/save", methods=["POST"])
@login_required
def save_daily_stats():
    uid = get_current_user_id()
    conn = get_db()
    today = datetime.date.today().isoformat()
    reminders = conn.execute("SELECT * FROM reminders WHERE user_id = ? AND date = ?", (uid, today,)).fetchall()
    total = len(reminders)
    completed = sum(1 for r in reminders if r["status"] == "done")
    missed = sum(1 for r in reminders if r["status"] == "missed")
    adherence = round(completed / total * 100, 1) if total > 0 else 0

    existing = conn.execute("SELECT * FROM daily_stats WHERE user_id = ? AND date = ?", (uid, today,)).fetchone()
    if existing:
        conn.execute("UPDATE daily_stats SET total_tasks=?, completed=?, missed=?, adherence=? WHERE user_id=? AND date=?",
                     (total, completed, missed, adherence, uid, today))
    else:
        conn.execute("INSERT INTO daily_stats (user_id, date, total_tasks, completed, missed, adherence) VALUES (?,?,?,?,?,?)",
                     (uid, today, total, completed, missed, adherence))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
