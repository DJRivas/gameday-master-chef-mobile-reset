import os, uuid, sqlite3
from flask import Flask, render_template, request, jsonify, g, Response, make_response, redirect, url_for, session

DATABASE = os.environ.get("DATABASE_URL", "ratings.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "set-a-secret-key")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "MASTERCHEF2025")

# Participants (Steve added)
ENTRANTS = ["Javier","Lindsay","Yesenia","Bryan","Viviana","Bernie","Rogelio","Daniella","Colleen","Justin","Paige","Nic","Martha","Steve"]

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = SECRET_KEY

def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DATABASE, check_same_thread=False)
        db.row_factory = sqlite3.Row
    return db

def column_exists(db, table, col):
    row = db.execute("PRAGMA table_info(%s)" % table).fetchall()
    return any(r[1] == col for r in row)

def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS ratings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entrant_index INTEGER NOT NULL,
            taste INTEGER NOT NULL CHECK(taste BETWEEN 1 AND 5),
            presentation INTEGER NOT NULL CHECK(presentation BETWEEN 1 AND 5),
            easy INTEGER NOT NULL CHECK(easy BETWEEN 1 AND 5),
            judge TEXT,
            device_id TEXT,
            one_word TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (entrant_index, device_id)
        )
    """)
    try:
        if not column_exists(db, "ratings", "one_word"):
            db.execute("ALTER TABLE ratings ADD COLUMN one_word TEXT")
    except Exception:
        pass
    db.commit()

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()

with app.app_context():
    init_db()

def device_id_from_request():
    return request.cookies.get("device_id") or "anon"

@app.route("/")
def home():
    resp = make_response(render_template("index.html", entrants=ENTRANTS, title="2025 GAME DAY MASTER CHEF COMPETITION CHALLENGE"))
    if not request.cookies.get("device_id"):
        resp.set_cookie("device_id", str(uuid.uuid4()), max_age=60*60*24*365, samesite="Lax")
    return resp

@app.route("/words")
def words_page():
    return render_template("words.html", entrants=ENTRANTS, title="One Word Results")

def sanitize_one_word(s):
    if not s:
        return None
    s = (s or "").strip()
    if not s:
        return None
    first = s.split()[0][:20]
    return first

@app.route("/api/rate", methods=["POST"])
def api_rate():
    data = request.get_json(silent=True) or {}
    try:
        entrant_index = int(data.get("entrant_index"))
        taste = int(data.get("taste"))
        presentation = int(data.get("presentation"))
        easy = int(data.get("easy"))
        judge = (data.get("judge") or "").strip()[:50] or None
        one_word = sanitize_one_word(data.get("one_word"))
    except Exception:
        return jsonify({"ok": False, "error": "Invalid payload"}), 400

    if not (0 <= entrant_index < len(ENTRANTS)):
        return jsonify({"ok": False, "error": "Invalid entrant"}), 400
    for v in (taste, presentation, easy):
        if v < 1 or v > 5:
            return jsonify({"ok": False, "error": "Scores must be 1 to 5"}), 400

    device_id = device_id_from_request()
    db = get_db()
    db.execute(
        """
        INSERT INTO ratings (entrant_index, taste, presentation, easy, judge, device_id, one_word)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entrant_index, device_id) DO UPDATE SET
            taste=excluded.taste,
            presentation=excluded.presentation,
            easy=excluded.easy,
            judge=excluded.judge,
            one_word=excluded.one_word
        """,
        (entrant_index, taste, presentation, easy, judge, device_id, one_word),
    )
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/my-rating")
def api_my_rating():
    try:
        entrant_index = int(request.args.get("entrant_index", "-1"))
    except Exception:
        return jsonify({"ok": False, "error": "Bad entrant index"}), 400
    if not (0 <= entrant_index < len(ENTRANTS)):
        return jsonify({"ok": True, "rating": None})

    device_id = device_id_from_request()
    db = get_db()
    row = db.execute(
        "SELECT taste, presentation, easy, judge, one_word FROM ratings WHERE entrant_index=? AND device_id=?",
        (entrant_index, device_id),
    ).fetchone()
    return jsonify({"ok": True, "rating": dict(row) if row else None})

@app.route("/api/leaderboard")
def api_leaderboard():
    db = get_db()
    rows = db.execute("""
        SELECT entrant_index,
               COUNT(*) AS votes,
               AVG(taste) AS avg_taste,
               AVG(presentation) AS avg_presentation,
               AVG(easy) AS avg_easy,
               AVG(taste + presentation + easy) AS avg_total
        FROM ratings
        GROUP BY entrant_index
        ORDER BY avg_total DESC
    """).fetchall()
    out = [{
        "name": ENTRANTS[r["entrant_index"]],
        "votes": r["votes"],
        "avg_taste": round(r["avg_taste"] or 0, 2),
        "avg_presentation": round(r["avg_presentation"] or 0, 2),
        "avg_easy": round(r["avg_easy"] or 0, 2),
        "avg_total": round(r["avg_total"] or 0, 2),
    } for r in rows]
    return jsonify(out)

@app.route("/api/words")
def api_words():
    db = get_db()
    rows = db.execute("""
        SELECT entrant_index, LOWER(TRIM(one_word)) AS w, COUNT(*) AS c
        FROM ratings
        WHERE one_word IS NOT NULL AND TRIM(one_word) != ''
        GROUP BY entrant_index, LOWER(TRIM(one_word))
        ORDER BY entrant_index ASC, c DESC, w ASC
    """).fetchall()
    out = {}
    for r in rows:
        name = ENTRANTS[r["entrant_index"]]
        out.setdefault(name, []).append({"word": r["w"], "count": r["c"]})
    return jsonify(out)

@app.route("/export.csv")
def export_csv():
    db = get_db()
    rows = db.execute("""
        SELECT id, entrant_index, taste, presentation, easy, judge, device_id, one_word, created_at
        FROM ratings
        ORDER BY created_at ASC
    """).fetchall()

    def generate():
        header = ["id","entrant_name","taste","presentation","easy","judge","device_id","one_word","created_at"]
        yield ",".join(header) + "\\n"
        for r in rows:
            name = ENTRANTS[r["entrant_index"]]
            def q(s): 
                s = "" if s is None else str(s)
                return '"' + s.replace('"','""') + '"'
            line = [
                str(r["id"]), q(name), str(r["taste"]), str(r["presentation"]), str(r["easy"]),
                q(r["judge"] or ""), q(r["device_id"] or ""), q(r["one_word"] or ""), str(r["created_at"])
            ]
            yield ",".join(line) + "\\n"
    return Response(generate(), mimetype="text/csv")

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        pw = (request.form.get("password") or "").strip()
        if pw == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin"))
        return render_template("admin_login.html", error="Incorrect password")
    if not session.get("is_admin"):
        return render_template("admin_login.html")

    db = get_db()
    rows = db.execute("""
        SELECT r.id, r.entrant_index, r.taste, r.presentation, r.easy, r.judge, r.device_id, r.one_word, r.created_at
        FROM ratings r
        ORDER BY r.entrant_index ASC, r.created_at ASC
    """).fetchall()
    detailed = [{
        "id": r["id"],
        "entrant": ENTRANTS[r["entrant_index"]],
        "taste": r["taste"],
        "presentation": r["presentation"],
        "easy": r["easy"],
        "total": r["taste"] + r["presentation"] + r["easy"],
        "judge": r["judge"] or "",
        "device_id": r["device_id"] or "",
        "one_word": r["one_word"] or "",
        "created_at": r["created_at"],
    } for r in rows]
    lb = db.execute("""
        SELECT entrant_index, COUNT(*) AS votes, AVG(taste + presentation + easy) AS avg_total
        FROM ratings GROUP BY entrant_index ORDER BY avg_total DESC
    """).fetchall()
    lb_data = [{"name": ENTRANTS[r["entrant_index"]], "votes": r["votes"], "avg_total": round(r["avg_total"] or 0, 2)} for r in lb]
    reset_ok = request.args.get("reset") == "1"
    return render_template("admin_results.html", detailed=detailed, leaderboard=lb_data, title="Admin Detailed Results", reset_ok=reset_ok)

@app.route("/admin/reset", methods=["POST"])
def admin_reset():
    if not session.get("is_admin"):
        return redirect(url_for("admin"))
    db = get_db()
    db.execute("DELETE FROM ratings")
    db.commit()
    return redirect(url_for("admin", reset="1"))

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
