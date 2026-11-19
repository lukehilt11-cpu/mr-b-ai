"""
Mr. B AI — desktop chat app (Flask + native window) backed by Google Gemini.
Chats are saved in a local SQLite database.

Run from source:  python app.py            (native window)
                  python app.py --browser  (opens your default browser instead)
"""
import json
import logging
import multiprocessing
import os
import secrets
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from contextlib import closing
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import (Flask, jsonify, redirect, render_template_string, request,
                   session, url_for)
from google import genai
from google.genai import types
from werkzeug.serving import make_server

# ───────────────────────── Config (edit these, then rebuild) ─────────────────────────
APP_NAME = "Mr. B AI"
PLACEHOLDER = "PASTE_YOUR_GEMINI_API_KEY_HERE"
DEFAULTS = {
    "gemini_api_key": PLACEHOLDER,
    "model": "gemini-3.5-flash-lite",   # fallback: "gemini-3.1-flash-lite"
    "username": "mrb",
    "password": "Health2026",
    "require_login": False,              # set to false in config.json to skip the login screen
}
MAX_HISTORY = 40                        # messages sent to the model per request
SYSTEM_PROMPT = """You are Mr. B AI, a specialist in information analysis and pattern recognition, and an outstanding teaching assistant.

You excel at:
- Building lesson plans (objectives, timing, activities, materials, differentiation, and a quick check for understanding).
- Creating assignments, rubrics, quizzes, and answer keys for high school students.
- Coming up with fresh, engaging ideas that give a classroom new experiences: projects, games, discussions, and hands-on activities.
- Spotting patterns in information such as grades, student work, and data, and turning them into clear insights and next steps.

How you work:
- Be practical and ready to use. Give the teacher something they can print or teach from today.
- Match the grade level, subject, and time available. If a key detail is missing, make a sensible assumption, state it briefly, and continue.
- Be concise by default. Use Markdown headings, lists, and tables when they help, and put code in fenced blocks.
- When useful, offer a variation, such as a harder version, an easier version, or a shorter one."""
# ──────────────────────────────────────────────────────────────────────────────────────


def data_dir() -> Path:
    """Per-user folder for chats.db, config.json and logs (survives reinstalls)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    p = base / "MrBAI"
    p.mkdir(parents=True, exist_ok=True)
    return p


DATA = data_dir()
DB_PATH = DATA / "chats.db"
CONFIG_PATH = DATA / "config.json"


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    saved = {}
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text("utf-8"))
        except Exception:
            logging.exception("Could not read config.json")
    for k, v in saved.items():                      # ignore blanks and the placeholder key
        if v not in ("", None, PLACEHOLDER):
            cfg[k] = v
    changed = not CONFIG_PATH.exists()
    for k in ("secret_key", "api_token"):           # generated once, then kept
        if not cfg.get(k):
            cfg[k] = secrets.token_hex(32) if k == "secret_key" else secrets.token_urlsafe(16)
            changed = True
    if changed:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), "utf-8")
    return cfg


cfg = load_config()
app = Flask(__name__)
app.secret_key = cfg["secret_key"]
app.permanent_session_lifetime = timedelta(days=90)

_client = None


def get_client():
    global _client
    key = cfg["gemini_api_key"]
    if not key or key == PLACEHOLDER:
        raise RuntimeError("No Gemini API key yet. Click “API key” at the top to add one.")
    if _client is None:
        _client = genai.Client(api_key=key)
    return _client


# ───────────────────────────── Database ─────────────────────────────
def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    with closing(db()) as c, c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
            CREATE TABLE IF NOT EXISTS chats(
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
        """)


def rows(sql, args=()):
    with closing(db()) as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def save_turn(chat_id, user_text, reply):
    now = time.time()
    with closing(db()) as c, c:
        if chat_id is None:
            title = " ".join(user_text.split())[:48] or "New chat"
            chat_id = c.execute("INSERT INTO chats(title, created, updated) VALUES(?,?,?)",
                                (title, now, now)).lastrowid
        else:
            c.execute("UPDATE chats SET updated=? WHERE id=?", (now, chat_id))
        c.executemany("INSERT INTO messages(chat_id, role, content, created) VALUES(?,?,?,?)",
                      [(chat_id, "user", user_text, now), (chat_id, "assistant", reply, now + 0.001)])
        title = c.execute("SELECT title FROM chats WHERE id=?", (chat_id,)).fetchone()["title"]
    return chat_id, title


# ───────────────────────────── Auth ─────────────────────────────
def authed():
    if not cfg["require_login"] or session.get("ok"):
        return True
    return request.headers.get("X-API-Key") == cfg["api_token"]


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not authed():
            if request.path.startswith("/api/"):
                return jsonify(error="Not signed in."), 401
            return redirect(url_for("login"))
        return view(*a, **kw)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (request.form.get("username") == cfg["username"]
                and request.form.get("password") == cfg["password"]):
            session["ok"] = True
            session.permanent = True
            return redirect(url_for("home"))
        error = "That username or password isn’t right."
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    if cfg["gemini_api_key"] in ("", PLACEHOLDER):
        return redirect(url_for("setup"))
    return render_template_string(CHAT_HTML, model=cfg["model"], signout=cfg["require_login"])


@app.route("/setup", methods=["GET", "POST"])
@login_required
def setup():
    global _client
    error = None
    if request.method == "POST":
        key = (request.form.get("key") or "").strip()
        if len(key) < 20 or " " in key:
            error = "That doesn’t look like a Gemini API key. Copy it again from Google AI Studio."
        else:
            cfg["gemini_api_key"] = key
            _client = None
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2), "utf-8")
            return redirect(url_for("home"))
    return render_template_string(SETUP_HTML, error=error)


# ───────────────────────────── API ─────────────────────────────
def ask(history):
    contents = [
        types.Content(role="user" if m["role"] == "user" else "model",
                      parts=[types.Part(text=m["content"])])
        for m in history[-MAX_HISTORY:]
    ]
    while contents and contents[0].role == "model":   # Gemini wants a user turn first
        contents.pop(0)
    resp = get_client().models.generate_content(
        model=cfg["model"], contents=contents,
        config=types.GenerateContentConfig(system_instruction=cfg.get("system_prompt") or SYSTEM_PROMPT))
    return resp.text or "(No text came back — try rephrasing.)"


@app.post("/api/chat")
@login_required
def chat():
    """Saved chat: {"chat_id": int|null, "message": "..."} -> {reply, chat_id, title}
    Stateless (for scripts): {"messages": [{"role","content"}, ...]} -> {reply}"""
    data = request.get_json(silent=True) or {}
    try:
        if "messages" in data:
            msgs = [m for m in data["messages"] if m.get("content", "").strip()]
            if not msgs or msgs[-1].get("role") != "user":
                return jsonify(error="Send at least one user message."), 400
            return jsonify(reply=ask(msgs))

        text = (data.get("message") or "").strip()
        if not text:
            return jsonify(error="Type a message first."), 400
        chat_id = data.get("chat_id")
        history = []
        if chat_id is not None:
            if not rows("SELECT 1 FROM chats WHERE id=?", (chat_id,)):
                return jsonify(error="That chat no longer exists."), 404
            history = rows("SELECT role, content FROM messages WHERE chat_id=? ORDER BY id", (chat_id,))
        reply = ask(history + [{"role": "user", "content": text}])
        chat_id, title = save_turn(chat_id, text, reply)   # saved only after a successful reply
        return jsonify(reply=reply, chat_id=chat_id, title=title)
    except Exception as e:
        logging.exception("chat failed")
        return jsonify(error=f"{type(e).__name__}: {e}"), 502


@app.get("/api/chats")
@login_required
def list_chats():
    return jsonify(rows("SELECT id, title, updated FROM chats ORDER BY updated DESC"))


@app.get("/api/chats/<int:cid>")
@login_required
def get_chat(cid):
    chat_row = rows("SELECT id, title FROM chats WHERE id=?", (cid,))
    if not chat_row:
        return jsonify(error="Chat not found."), 404
    msgs = rows("SELECT role, content FROM messages WHERE chat_id=? ORDER BY id", (cid,))
    return jsonify(**chat_row[0], messages=msgs)


@app.delete("/api/chats/<int:cid>")
@login_required
def delete_chat(cid):
    with closing(db()) as c, c:
        c.execute("DELETE FROM chats WHERE id=?", (cid,))
    return jsonify(ok=True)


# ───────────────────────────── Templates ─────────────────────────────
STYLE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#F4F6FA;--panel:#fff;--line:#DCE2EE;--ink:#0F1B33;--muted:#5B6780;--accent:#2457D6;--accent-ink:#fff;--soft:#EAF0FD;--navy:#0F1B33;--err:#B42318}
@media (prefers-color-scheme:dark){:root{--bg:#0B1120;--panel:#121A2D;--line:#25304A;--ink:#E7ECF7;--muted:#93A0BC;--accent:#6F9BFF;--accent-ink:#0B1120;--soft:#18223A;--navy:#070C18;--err:#FDA29B}}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{font-family:"Manrope",system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--ink);line-height:1.55;-webkit-font-smoothing:antialiased}
.mark{display:inline-grid;place-items:center;width:32px;height:32px;border-radius:9px;background:var(--accent);color:var(--accent-ink);font-weight:800;font-size:17px}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px;letter-spacing:-.01em}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
</style>
"""

LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · Mr. B AI</title>""" + STYLE + """
<style>
.split{display:grid;grid-template-columns:1.05fr 1fr;min-height:100%}
.side{background:var(--navy);color:#E7ECF7;padding:56px;display:flex;flex-direction:column;justify-content:space-between}
.side h1{font-size:clamp(34px,4.4vw,56px);line-height:1.05;letter-spacing:-.03em;margin:0 0 16px;font-weight:800;max-width:11ch}
.side p{color:#9FB0D3;max-width:34ch;margin:0;font-size:16px}
.side .mark{background:#6F9BFF;color:#0B1120}
.form{display:grid;place-items:center;padding:32px}
form{width:100%;max-width:360px}
form h2{font-size:24px;margin:0 0 6px;letter-spacing:-.02em}
form .sub{color:var(--muted);margin:0 0 26px}
label{display:block;font-weight:600;font-size:14px;margin:16px 0 6px}
input{width:100%;font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
input:focus{outline:2px solid var(--accent);outline-offset:0;border-color:var(--accent)}
button{width:100%;margin-top:24px;font:inherit;font-weight:700;background:var(--accent);color:var(--accent-ink);border:0;border-radius:10px;padding:13px;cursor:pointer}
button:hover{filter:brightness(1.08)}
.err{background:color-mix(in srgb,var(--err) 12%,transparent);color:var(--err);border-radius:10px;padding:10px 12px;font-size:14px;margin-bottom:6px}
@media (max-width:820px){.split{grid-template-columns:1fr}.side{padding:28px 24px 32px;gap:28px}.side h1{font-size:34px;max-width:none}}
</style></head><body>
<div class="split">
  <section class="side">
    <div class="brand"><span class="mark">B</span>Mr. B AI</div>
    <div><h1>Your classroom assistant, ready when you are.</h1><p>Private, fast, and powered by Gemini.</p></div>
  </section>
  <section class="form">
    <form method="post" autocomplete="on">
      <h2>Sign in</h2><p class="sub">Enter your details to open Mr. B AI.</p>
      {% if error %}<div class="err" role="alert">{{ error }}</div>{% endif %}
      <label for="u">Username</label><input id="u" name="username" autocomplete="username" autofocus required>
      <label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Sign in</button>
    </form>
  </section>
</div></body></html>"""

SETUP_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>API key · Mr. B AI</title>""" + STYLE + """
<style>
body{display:grid;place-items:center;padding:24px}
form{width:100%;max-width:440px;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:32px}
h1{font-size:24px;letter-spacing:-.02em;margin:18px 0 6px}
.sub{color:var(--muted);margin:0 0 22px}
.sub a{color:var(--accent)}
label{display:block;font-weight:600;font-size:14px;margin:0 0 6px}
input{width:100%;font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
input:focus{outline:2px solid var(--accent);outline-offset:0;border-color:var(--accent)}
button{width:100%;margin-top:20px;font:inherit;font-weight:700;background:var(--accent);color:var(--accent-ink);border:0;border-radius:10px;padding:13px;cursor:pointer}
.back{display:block;text-align:center;margin-top:14px;color:var(--muted);font-size:14px}
.err{background:color-mix(in srgb,var(--err) 12%,transparent);color:var(--err);border-radius:10px;padding:10px 12px;font-size:14px;margin-bottom:16px}
</style></head><body>
<form method="post">
  <div class="brand"><span class="mark">B</span>Mr. B AI</div>
  <h1>Add your Gemini API key</h1>
  <p class="sub">Mr. B AI uses your own key and stores it only on this computer. Get a free one from <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">Google AI Studio</a>.</p>
  {% if error %}<div class="err" role="alert">{{ error }}</div>{% endif %}
  <label for="k">API key</label>
  <input id="k" name="key" type="password" autocomplete="off" spellcheck="false" autofocus required>
  <button type="submit">Save and start</button>
  <a class="back" href="/">Cancel</a>
</form></body></html>"""

CHAT_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Mr. B AI</title>""" + STYLE + """
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.6/purify.min.js"></script>
<style>
body{display:grid;grid-template-columns:272px 1fr;height:100vh;height:100dvh;overflow:hidden}
aside{display:flex;flex-direction:column;background:var(--panel);border-right:1px solid var(--line);min-height:0}
.side-top{padding:16px 14px 10px;display:flex;flex-direction:column;gap:14px}
.btn{font:inherit;font-size:14px;font-weight:600;color:var(--ink);background:transparent;border:1px solid var(--line);border-radius:9px;padding:8px 12px;cursor:pointer;text-decoration:none;display:inline-block}
.btn:hover{background:var(--soft)}
.btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);text-align:center}
.btn.primary:hover{filter:brightness(1.08);background:var(--accent)}
#list{flex:1;overflow-y:auto;padding:4px 8px 14px}
.label{font-size:12px;font-weight:600;color:var(--muted);padding:8px 8px 4px}
.item{display:flex;align-items:center;border-radius:9px}
.item:hover,.item.active{background:var(--soft)}
.item .open{flex:1;min-width:0;text-align:left;font:inherit;font-size:14px;color:var(--ink);background:none;border:0;padding:9px 10px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item.active .open{font-weight:700}
.item .del{font:inherit;font-size:13px;color:var(--muted);background:none;border:0;border-radius:7px;padding:5px 8px;margin-right:4px;cursor:pointer;visibility:hidden}
.item:hover .del,.item .del.arm{visibility:visible}
.item .del.arm{color:var(--err);font-weight:700}
.nochats{color:var(--muted);font-size:14px;padding:8px 10px}
.col-main{display:flex;flex-direction:column;min-width:0;min-height:0}
header{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 20px;background:var(--panel);border-bottom:1px solid var(--line)}
.left{display:flex;align-items:center;gap:10px;min-width:0}
#title{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.menu{display:none}
.right{display:flex;align-items:center;gap:8px}
.chip{font-size:12px;font-weight:600;color:var(--muted);background:var(--soft);padding:4px 10px;border-radius:999px}
main{flex:1;overflow-y:auto;padding:28px 16px 12px}
.col{max-width:760px;margin:0 auto}
.empty{text-align:center;padding:9vh 0 0}
.empty h1{font-size:clamp(28px,4vw,40px);letter-spacing:-.03em;margin:0 0 8px;font-weight:800}
.empty p{color:var(--muted);margin:0 0 28px}
.starters{display:flex;flex-wrap:wrap;gap:10px;justify-content:center}
.starters button{font:inherit;font-size:14px;font-weight:500;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 14px;cursor:pointer}
.starters button:hover{border-color:var(--accent)}
.msg{display:flex;margin:0 0 18px}
.msg.user{justify-content:flex-end}
.bubble{max-width:88%;padding:11px 15px;border-radius:16px;overflow-wrap:anywhere}
.user .bubble{background:var(--accent);color:var(--accent-ink);border-bottom-right-radius:5px;white-space:pre-wrap}
.bot .bubble{background:var(--panel);border:1px solid var(--line);border-bottom-left-radius:5px}
.bot .bubble>:first-child{margin-top:0}.bot .bubble>:last-child{margin-bottom:0}
.bubble pre{background:var(--navy);color:#E7ECF7;padding:12px 14px;border-radius:10px;overflow-x:auto;font-size:13px}
.bubble code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em}
.bubble :not(pre)>code{background:var(--soft);padding:1px 5px;border-radius:5px}
.bubble table{border-collapse:collapse;display:block;overflow-x:auto}
.bubble th,.bubble td{border:1px solid var(--line);padding:6px 10px}
.bubble a{color:var(--accent)}
.bubble.error{color:var(--err)}
.dots{display:inline-flex;gap:5px;padding:4px 0}
.dots i{width:7px;height:7px;border-radius:50%;background:var(--muted);animation:b 1.2s infinite ease-in-out}
.dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}
@keyframes b{0%,80%,100%{opacity:.3;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}
@media (prefers-reduced-motion:reduce){.dots i{animation:none;opacity:.7}}
footer{padding:10px 16px 18px}
.composer{max-width:760px;margin:0 auto;display:flex;gap:10px;align-items:flex-end;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:8px 8px 8px 16px}
.composer:focus-within{border-color:var(--accent)}
textarea{flex:1;resize:none;border:0;background:transparent;color:var(--ink);font:inherit;max-height:200px;padding:7px 0;outline:none}
#send{width:40px;height:40px;border:0;border-radius:11px;background:var(--accent);color:var(--accent-ink);cursor:pointer;display:grid;place-items:center}
#send:disabled{opacity:.45;cursor:default}
.hint{text-align:center;color:var(--muted);font-size:12px;margin:8px 0 0}
.scrim{display:none}
@media (max-width:820px){
  body{grid-template-columns:1fr}
  aside{position:fixed;inset:0 auto 0 0;width:min(300px,84vw);z-index:20;transform:translateX(-100%);transition:transform .2s}
  body.nav aside{transform:none}
  body.nav .scrim{display:block;position:fixed;inset:0;background:rgba(5,10,25,.45);z-index:10}
  .menu{display:inline-block;padding:6px 10px}
}
@media (prefers-reduced-motion:reduce){aside{transition:none}}
</style></head><body>
<aside>
  <div class="side-top">
    <div class="brand"><span class="mark">B</span>Mr. B AI</div>
    <button class="btn primary" id="new">New chat</button>
  </div>
  <div class="label">Recent chats</div>
  <div id="list"></div>
</aside>
<div class="scrim" id="scrim"></div>
<div class="col-main">
  <header>
    <div class="left"><button class="btn menu" id="menu" aria-label="Open chat list">Chats</button><span id="title">New chat</span></div>
    <div class="right"><span class="chip">{{ model }}</span><a class="btn" href="/setup">API key</a>{% if signout %}<a class="btn" href="/logout">Sign out</a>{% endif %}</div>
  </header>
  <main id="main"><div class="col" id="log"></div></main>
  <footer>
    <div class="composer">
      <textarea id="box" rows="1" placeholder="Message Mr. B AI" aria-label="Message"></textarea>
      <button id="send" aria-label="Send message"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
    </div>
    <p class="hint">Enter to send · Shift+Enter for a new line</p>
  </footer>
</div>
<script>
const $=id=>document.getElementById(id);
const log=$('log'),box=$('box'),send=$('send'),main=$('main'),list=$('list'),title=$('title');
let chatId=null,busy=false,errEl=null;
const starters=["Plan a 50-minute lesson","Write an assignment with a rubric","Give me a fresh classroom activity idea","Find patterns in my students' results"];
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const md=t=>(window.marked&&window.DOMPurify)?DOMPurify.sanitize(marked.parse(t,{breaks:true})):'<div style="white-space:pre-wrap">'+esc(t)+'</div>';
const scroll=()=>{main.scrollTop=main.scrollHeight};
const closeNav=()=>document.body.classList.remove('nav');
async function api(url,opts){
  const r=await fetch(url,opts);
  if(r.status===401){location='/login';throw new Error('Signed out')}
  const d=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(d.error||'Request failed');
  return d;
}
function add(role,html,cls){
  const m=document.createElement('div');m.className='msg '+(role==='user'?'user':'bot');
  const b=document.createElement('div');b.className='bubble'+(cls?' '+cls:'');
  if(role==='user')b.textContent=html;else b.innerHTML=html;
  m.appendChild(b);log.appendChild(m);scroll();return m;
}
function showEmpty(){
  log.innerHTML='<div class="empty"><h1>How can I help today?</h1><p>Lesson plans, assignments, and fresh classroom ideas. Start with one of these or ask anything.</p><div class="starters"></div></div>';
  const s=log.querySelector('.starters');
  starters.forEach(t=>{const b=document.createElement('button');b.textContent=t;b.onclick=()=>{box.value=t;go()};s.appendChild(b)});
}
async function loadChats(){
  let chats=[];try{chats=await api('/api/chats')}catch(e){return}
  list.innerHTML='';
  if(!chats.length){list.innerHTML='<div class="nochats">Your chats will appear here.</div>';return}
  chats.forEach(c=>{
    const row=document.createElement('div');row.className='item'+(c.id===chatId?' active':'');
    const o=document.createElement('button');o.className='open';o.textContent=c.title;o.title=c.title;o.onclick=()=>openChat(c.id);
    const x=document.createElement('button');x.className='del';x.textContent='×';x.setAttribute('aria-label','Delete chat');
    x.onclick=async e=>{
      e.stopPropagation();
      if(!x.classList.contains('arm')){x.classList.add('arm');x.textContent='Delete?';setTimeout(()=>{x.classList.remove('arm');x.textContent='×'},3000);return}
      await api('/api/chats/'+c.id,{method:'DELETE'});
      if(c.id===chatId)newChat();else loadChats();
    };
    row.append(o,x);list.appendChild(row);
  });
}
async function openChat(id){
  const d=await api('/api/chats/'+id);
  chatId=id;title.textContent=d.title;log.innerHTML='';errEl=null;
  d.messages.forEach(m=>add(m.role,m.role==='user'?m.content:md(m.content)));
  closeNav();loadChats();scroll();box.focus();
}
function newChat(){chatId=null;errEl=null;title.textContent='New chat';showEmpty();closeNav();loadChats();box.focus()}
async function go(){
  const text=box.value.trim();if(!text||busy)return;
  if(errEl){errEl.remove();errEl=null}
  if(log.querySelector('.empty'))log.innerHTML='';
  busy=true;send.disabled=true;box.value='';grow();
  const u=add('user',text);
  const wait=add('bot','<span class="dots"><i></i><i></i><i></i></span>');
  try{
    const d=await api('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:chatId,message:text})});
    wait.remove();add('bot',md(d.reply));
    chatId=d.chat_id;title.textContent=d.title;loadChats();
  }catch(e){
    wait.remove();u.remove();box.value=text;grow();
    errEl=add('bot',esc(e.message),'error');
  }
  busy=false;send.disabled=false;box.focus();
}
function grow(){box.style.height='auto';box.style.height=Math.min(box.scrollHeight,200)+'px'}
box.addEventListener('input',grow);
box.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();go()}});
send.onclick=go;
$('new').onclick=newChat;
$('menu').onclick=()=>document.body.classList.add('nav');
$('scrim').onclick=closeNav;
showEmpty();loadChats();box.focus();
</script></body></html>"""


# ───────────────────────────── Launcher ─────────────────────────────
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    multiprocessing.freeze_support()
    for name in ("stdout", "stderr"):                 # windowed builds have no console
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    logging.basicConfig(filename=str(DATA / "app.log"), level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    init_db()
    port = free_port()
    (DATA / "port.txt").write_text(str(port))         # lets your own scripts find the local API
    url = f"http://127.0.0.1:{port}/"
    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    window = "--browser" not in sys.argv
    if window:
        try:
            import webview
        except ImportError:
            window = False
    if window:
        webview.create_window(APP_NAME, url, width=1180, height=800, min_size=(760, 520))
        webview.start(private_mode=False, storage_path=str(DATA / "webview"))
    else:
        webbrowser.open(url)
        print(f"{APP_NAME} is running at {url}  (Ctrl+C to quit)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    server.shutdown()


if __name__ == "__main__":
    main()
