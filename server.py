#!/usr/bin/env python3
"""
College CTF — 1.5 Hour Event
Full web platform: scoreboard, 3 challenges, flag submission, login for Round 3.
Pure Python stdlib — no external dependencies.
"""

import http.server
import socketserver
import urllib.parse
import json
import hashlib
import os
import time
import threading
import html
import base64
from datetime import datetime

HOST = "0.0.0.0"
# Render (and most PaaS) inject PORT via environment variable
PORT = int(os.environ.get("PORT", "8080"))
BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, "data.json")
STATIC_DIR = os.path.join(BASE, "static")

# Event timer: default 1 hour 30 minutes. Admin can start/stop/reset from /admin
EVENT_DURATION_SEC = int(1.5 * 3600)  # 5400 seconds = 1h 30m
ADMIN_PASSWORD = "cyber@123"  # change this before the event


# ---- Challenge definitions ----
CHALLENGES = {
    "1": {
        "id": "1",
        "name": "Campus Cipher",
        "category": "Crypto",
        "difficulty": "Easy",
        "points": 30,
        "description": (
            "A senior left this message on the notice board:\n\n"
            "ZmxhZ3tjYW1wdXNfY2lwaGVyX2lzX2Vhc3l9\n\n"
            "He also wrote underneath: \"First decode it.\"\n\n"
            "Find the flag."
        ),
        "flag": "flag{campus_cipher_is_easy}",
        "file": None,
    },
    "2": {
        "id": "2",
        "name": "Hidden Notice",
        "category": "Forensics",
        "difficulty": "Medium",
        "points": 30,
        "description": (
            "The student council uploaded an image. We suspect they hid a secret code inside it.\n\n"
            "Download notice.png and look deeper than the visible pixels.\n\n"
            "Something is appended to the file. Extract it."
        ),
        "flag": "flag{hidden_in_the_pixels}",
        "file": "notice.png",
    },
    "3": {
        "id": "3",
        "name": "Broken Login",
        "category": "Web",
        "difficulty": "Hard",
        "points": 40,
        "description": (
            "There is a vulnerable login form on this platform.\n\n"
            "Go to the Login page (or /login) and bypass authentication.\n\n"
            "The developer left a note in the page source:\n"
            "/* TODO: sanitize input before SQL query */\n\n"
            "Bypass the login. The flag is shown after successful access."
        ),
        "flag": "flag{sql_still_works_in_2026}",
        "file": None,
    },
}

# In-memory store (persisted to data.json)
lock = threading.Lock()
store = {
    "teams": {},       # team_name -> {password_hash, solves: {chal_id: timestamp}, score}
    "submissions": [], # log
    "start_time": None,  # unix timestamp when event started (None = not started)
    "frozen": False,   # admin freeze
    "running": False,  # admin must start the clock
    "duration_sec": EVENT_DURATION_SEC,  # adjustable duration
}


def load_store():
    global store
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                store = json.load(f)
        except Exception:
            pass


def save_store():
    with open(DATA_FILE, "w") as f:
        json.dump(store, f, indent=2)


def event_remaining():
    """Return (remaining_seconds, frozen, status_label).
    Clock only runs after admin presses Start.
    """
    with lock:
        running = store.get("running", False)
        frozen_flag = store.get("frozen", False)
        start = store.get("start_time")
        duration = store.get("duration_sec", EVENT_DURATION_SEC)

    if not running or start is None:
        # Not started yet — show full duration, not frozen for display, but block submits
        return duration, True, "WAITING"
    elapsed = time.time() - start
    remaining = max(0, duration - elapsed)
    if frozen_flag or remaining <= 0:
        return remaining, True, "FROZEN"
    return remaining, False, "RUNNING"


def format_remaining(secs):
    secs = int(max(0, secs))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h:01d}:{m:02d}:{s:02d}"


def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def get_scoreboard():
    rows = []
    for name, t in store["teams"].items():
        solves = t.get("solves", {})
        score = sum(CHALLENGES[cid]["points"] for cid in solves if cid in CHALLENGES)
        last = max(solves.values()) if solves else 0
        rows.append({
            "name": name,
            "score": score,
            "solves": len(solves),
            "last": last,
        })
    rows.sort(key=lambda x: (-x["score"], x["last"]))
    return rows


# ---- HTML templates ----
def page(title, body, team=None, remaining=None, frozen=False, status="RUNNING"):
    if remaining is None:
        try:
            remaining, frozen, status = event_remaining()
        except Exception:
            remaining, frozen, status = EVENT_DURATION_SEC, True, "WAITING"
    frozen_flag = "1" if frozen else "0"
    remaining = int(remaining)
    status = status or "RUNNING"
    nav_team = f'<span class="team">Player: <b>{html.escape(team)}</b></span>' if team else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — College CTF</title>
<style>
:root {{
  --bg: #0f172a;
  --card: #1e293b;
  --border: #334155;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --green: #4ade80;
  --orange: #fb923c;
  --red: #f87171;
  --purple: #a78bfa;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  line-height: 1.5;
}}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
header {{
  background: #020617;
  border-bottom: 1px solid var(--border);
  padding: 0.9rem 1.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.5rem;
}}
.logo {{ font-weight: 700; font-size: 1.15rem; color: var(--accent); }}
nav a {{ margin-left: 1.2rem; color: var(--muted); font-size: 0.95rem; }}
nav a:hover {{ color: var(--accent); }}
.team {{ color: var(--green); font-size: 0.9rem; }}
main {{ max-width: 920px; margin: 0 auto; padding: 1.5rem; }}
h1 {{ font-size: 1.6rem; margin-bottom: 0.4rem; }}
h2 {{ font-size: 1.25rem; margin: 1.2rem 0 0.6rem; color: var(--accent); }}
.sub {{ color: var(--muted); margin-bottom: 1.2rem; font-size: 0.95rem; }}
.card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.2rem 1.4rem;
  margin-bottom: 1rem;
}}
.grid {{ display: grid; gap: 1rem; }}
@media (min-width: 640px) {{
  .grid-3 {{ grid-template-columns: repeat(3, 1fr); }}
}}
.badge {{
  display: inline-block;
  font-size: 0.75rem;
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
  font-weight: 600;
}}
.easy {{ background: #14532d; color: var(--green); }}
.medium {{ background: #7c2d12; color: var(--orange); }}
.hard {{ background: #7f1d1d; color: var(--red); }}
.points {{ color: var(--purple); font-weight: 700; }}
.chal-title {{ font-size: 1.1rem; font-weight: 600; margin: 0.3rem 0; }}
pre, .mono {{
  background: #020617;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.8rem 1rem;
  font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
  font-size: 0.88rem;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}}
input[type=text], input[type=password] {{
  width: 100%;
  padding: 0.6rem 0.8rem;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: #020617;
  color: var(--text);
  font-size: 1rem;
  margin: 0.4rem 0 0.8rem;
}}
button, .btn {{
  display: inline-block;
  background: var(--accent);
  color: #0f172a;
  border: none;
  padding: 0.55rem 1.2rem;
  border-radius: 6px;
  font-weight: 600;
  cursor: pointer;
  font-size: 0.95rem;
}}
button:hover, .btn:hover {{ filter: brightness(1.1); }}
.btn-outline {{
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text);
}}
.msg {{ padding: 0.7rem 1rem; border-radius: 6px; margin-bottom: 1rem; }}
.ok {{ background: #14532d; color: var(--green); border: 1px solid #166534; }}
.err {{ background: #7f1d1d; color: var(--red); border: 1px solid #991b1b; }}
.info {{ background: #1e3a5f; color: var(--accent); border: 1px solid #1e40af; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.95rem; }}
th, td {{ padding: 0.55rem 0.7rem; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; }}
tr:hover td {{ background: #1e293b88; }}
.solved {{ color: var(--green); }}
footer {{
  text-align: center;
  color: var(--muted);
  font-size: 0.8rem;
  padding: 2rem 1rem;
  border-top: 1px solid var(--border);
  margin-top: 2rem;
}}
.hint {{ color: var(--muted); font-size: 0.9rem; margin-top: 0.6rem; }}
.download {{ margin: 0.8rem 0; }}
</style>
</head>
<body data-remaining="{remaining}" data-frozen="{frozen_flag}" data-status="{status}">
<header>
  <div class="logo">College CTF</div>
  <nav>
    <a href="/">Home</a>
    <a href="/challenges">Challenges</a>
    <a href="/scoreboard">Scoreboard</a>
    <a href="/login">Login (Round 3)</a>
    <a href="/register">Register</a>
    <a href="/admin">Admin</a>
    {nav_team}
    <span id="timer" class="team" style="margin-left:1rem;color:var(--orange)">⏱ --:--:--</span>
  </nav>
</header>
<main>
{body}
</main>
<script>
(function(){{
  var el = document.getElementById('timer');
  if (!el) return;
  var rem = parseInt(document.body.getAttribute('data-remaining') || '0', 10);
  var frozen = document.body.getAttribute('data-frozen') === '1';
  var status = document.body.getAttribute('data-status') || 'RUNNING';
  function fmt(r){{
    var h = Math.floor(r/3600), m = Math.floor((r%3600)/60), s = r%60;
    return h + ':' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
  }}
  function tick(){{
    if (status === 'WAITING') {{
      el.textContent = '⏱ WAITING';
      el.style.color = 'var(--muted)';
      return;
    }}
    if (frozen || rem <= 0 || status === 'FROZEN') {{
      el.textContent = '⏱ FROZEN';
      el.style.color = 'var(--red)';
      return;
    }}
    el.textContent = '⏱ ' + fmt(rem);
    el.style.color = 'var(--orange)';
    rem -= 1;
    setTimeout(tick, 1000);
  }}
  tick();
}})();
</script>
<footer>
  College CTF — 1.5 Hour Event &nbsp;|&nbsp; Flag format: flag{{...}} &nbsp;|&nbsp; Total: 100 pts
</footer>
</body>
</html>"""


def home_page(team=None):
    body = """
    <h1>College CTF — 1.5 Hour Event</h1>
    <p class="sub">1.5 hours · 3 rounds · Easy → Medium → Hard · Total 100 points</p>

    <div class="card">
      <h2 style="margin-top:0">Schedule</h2>
      <table>
        <tr><th>Time</th><th>Activity</th></tr>
        <tr><td>0:00 – 0:10</td><td>Intro & rules</td></tr>
        <tr><td>0:10 – 0:35</td><td>Round 1 — Easy (Crypto)</td></tr>
        <tr><td>0:35 – 1:05</td><td>Round 2 — Medium (Forensics)</td></tr>
        <tr><td>1:05 – 1:30</td><td>Round 3 — Hard (Web) + scoring</td></tr>
      </table>
    </div>

    <div class="grid grid-3" style="margin-top:1rem">
      <div class="card">
        <span class="badge easy">EASY</span>
        <div class="chal-title">Campus Cipher</div>
        <div class="points">30 pts</div>
        <p style="color:var(--muted);font-size:0.9rem;margin-top:0.4rem">Crypto / Encoding</p>
      </div>
      <div class="card">
        <span class="badge medium">MEDIUM</span>
        <div class="chal-title">Hidden Notice</div>
        <div class="points">30 pts</div>
        <p style="color:var(--muted);font-size:0.9rem;margin-top:0.4rem">Forensics + Stego</p>
      </div>
      <div class="card">
        <span class="badge hard">HARD</span>
        <div class="chal-title">Broken Login</div>
        <div class="points">40 pts</div>
        <p style="color:var(--muted);font-size:0.9rem;margin-top:0.4rem">Web (SQLi)</p>
      </div>
    </div>

    <div class="card" style="margin-top:1rem">
      <p><b>How to play</b></p>
      <ol style="margin:0.5rem 0 0 1.2rem;color:var(--muted)">
        <li>Register your name</li>
        <li>Open Challenges and solve them in order</li>
        <li>Submit flags on each challenge page</li>
        <li>Watch the live scoreboard</li>
      </ol>
      <p style="margin-top:0.8rem"><a class="btn" href="/register">Register</a>
      &nbsp; <a class="btn btn-outline" href="/challenges">View Challenges</a></p>
    </div>
    """
    return page("Home", body, team)


def challenges_page(team=None, msg=None):
    solves = {}
    if team and team in store["teams"]:
        solves = store["teams"][team].get("solves", {})

    cards = ""
    for cid, c in CHALLENGES.items():
        diff_class = c["difficulty"].lower()
        solved = cid in solves
        status = '<span class="solved">✓ Solved</span>' if solved else ""
        cards += f"""
        <div class="card">
          <span class="badge {diff_class}">{c['difficulty']}</span>
          <span class="points">{c['points']} pts</span> {status}
          <div class="chal-title"><a href="/challenge/{cid}">{html.escape(c['name'])}</a></div>
          <p style="color:var(--muted);font-size:0.9rem">{c['category']}</p>
        </div>
        """

    msg_html = f'<div class="msg {msg[0]}">{html.escape(msg[1])}</div>' if msg else ""
    body = f"""
    <h1>Challenges</h1>
    <p class="sub">Solve all three. Submit the flag on each challenge page.</p>
    {msg_html}
    {cards}
    """
    return page("Challenges", body, team)


def challenge_page(cid, team=None, msg=None):
    c = CHALLENGES.get(cid)
    if not c:
        return page("Not Found", "<h1>Challenge not found</h1>", team)

    solves = {}
    if team and team in store["teams"]:
        solves = store["teams"][team].get("solves", {})
    solved = cid in solves

    msg_html = ""
    if msg:
        msg_html = f'<div class="msg {msg[0]}">{html.escape(msg[1])}</div>'
    if solved:
        msg_html = f'<div class="msg ok">You already solved this challenge.</div>'

    file_html = ""
    if c.get("file"):
        file_html = f'<div class="download"><a class="btn" href="/download/{c["file"]}">Download {c["file"]}</a></div>'

    form = ""
    if not solved:
        form = f"""
        <form method="POST" action="/submit/{cid}">
          <label>Flag</label>
          <input type="text" name="flag" placeholder="flag{{...}}" required autocomplete="off">
          <input type="hidden" name="team" value="{html.escape(team or '')}">
          <button type="submit">Submit Flag</button>
        </form>
        """
        if not team:
            form = '<div class="msg info">Register first, then come back to submit.</div><p><a href="/register">Register</a></p>'

    body = f"""
    <h1>{html.escape(c['name'])}</h1>
    <p class="sub">
      <span class="badge {c['difficulty'].lower()}">{c['difficulty']}</span>
      &nbsp; {c['category']} &nbsp;·&nbsp; <span class="points">{c['points']} pts</span>
    </p>
    {msg_html}
    <div class="card">
      <pre>{html.escape(c['description'])}</pre>
      {file_html}
    </div>
    <div class="card">
      <h2 style="margin-top:0">Submit Flag</h2>
      {form}
    </div>
    <p><a href="/challenges">← Back to challenges</a></p>
    """
    return page(c["name"], body, team)


def scoreboard_page(team=None):
    rows = get_scoreboard()
    table_rows = ""
    for i, r in enumerate(rows, 1):
        table_rows += f"""
        <tr>
          <td>{i}</td>
          <td>{html.escape(r['name'])}</td>
          <td class="points">{r['score']}</td>
          <td>{r['solves']}/3</td>
        </tr>
        """
    if not table_rows:
        table_rows = '<tr><td colspan="4" style="color:var(--muted)">No players yet. Register and solve!</td></tr>'

    body = f"""
    <h1>Scoreboard</h1>
    <p class="sub">Live ranking · Auto-refreshes on reload</p>
    <div class="card">
      <table>
        <tr><th>#</th><th>Player</th><th>Score</th><th>Solves</th></tr>
        {table_rows}
      </table>
    </div>
    <p style="margin-top:0.8rem"><a class="btn btn-outline" href="/scoreboard">Refresh</a></p>
    """
    return page("Scoreboard", body, team)


def register_page(msg=None, team=None):
    msg_html = f'<div class="msg {msg[0]}">{html.escape(msg[1])}</div>' if msg else ""
    body = f"""
    <h1>Register</h1>
    <p class="sub">Enter your name. You will use it when submitting flags.</p>
    {msg_html}
    <div class="card">
      <form method="POST" action="/register">
        <label>Your Name</label>
        <input type="text" name="team" required maxlength="32" placeholder="e.g. Rahul">
        <label>Password (optional, for reclaiming)</label>
        <input type="password" name="password" placeholder="optional">
        <button type="submit">Register</button>
      </form>
    </div>
    """
    return page("Register", body, team)


def login_page(msg=None, team=None, show_flag=False):
    """Round 3 — intentionally vulnerable SQLi login."""
    msg_html = ""
    if msg:
        msg_html = f'<div class="msg {msg[0]}">{html.escape(msg[1])}</div>'
    if show_flag:
        msg_html = f'''
        <div class="msg ok">
          <b>Access Granted — Admin Panel</b><br><br>
          Welcome, admin.<br>
          Flag: <span class="mono" style="display:inline;background:transparent;border:none;color:var(--green);font-weight:700">flag{{sql_still_works_in_2026}}</span>
        </div>
        <p class="hint">Copy the flag above and submit it on the <a href="/challenge/3">Broken Login challenge page</a> to score points.</p>
        '''

    # Intentionally leave comment in HTML source for students
    body = f"""
    <h1>Admin Login</h1>
    <p class="sub">College portal authentication (Round 3 challenge)</p>
    <!-- TODO: sanitize input before SQL query -->
    {msg_html}
    <div class="card">
      <form method="POST" action="/login">
        <label>Username</label>
        <input type="text" name="user" required autocomplete="off">
        <label>Password</label>
        <input type="password" name="pass" autocomplete="off">
        <button type="submit">Login</button>
      </form>
    </div>
    <p class="hint">This is the vulnerable login for Challenge 3. Bypass it to reveal the flag.</p>
    """
    return page("Login", body, team)


# ---- Fake SQL check (intentionally vulnerable logic) ----
def fake_sql_login(user, password):
    """
    Simulates: SELECT * FROM users WHERE username='$user' AND password='$pass'
    Classic SQLi patterns grant access.
    """
    u = user.strip()
    p = password.strip()

    # Real admin (optional)
    if u == "admin" and p == "supersecret_admin_pass_not_the_flag":
        return True

    # SQLi detection patterns (educational — not real SQL)
    combined = (u + " " + p).lower()
    payloads = [
        "' or '",
        "' or 1=1",
        "' or '1'='1",
        "admin'--",
        "admin' --",
        "' or 1=1--",
        "' or 1=1 --",
        "1' or '1'='1",
        "' or ''='",
        "or 1=1",
        "' or true--",
        "admin'/*",
    ]
    for pay in payloads:
        if pay in combined:
            return True

    # Also accept if username itself is a payload
    if any(x in u.lower() for x in ["' or", "or 1=1", "admin'--", "admin' --"]):
        return True

    return False



def admin_page(msg=None):
    remaining, frozen, status = event_remaining()
    msg_html = f'<div class="msg {msg[0]}">{html.escape(msg[1])}</div>' if msg else ""
    duration = store.get("duration_sec", EVENT_DURATION_SEC)
    h = duration // 3600
    m = (duration % 3600) // 60
    body = f"""
    <h1>Admin Panel</h1>
    <p class="sub">Control event timer · password protected</p>
    {msg_html}
    <div class="card">
      <p><b>Status:</b> {html.escape(status)}</p>
      <p><b>Time left:</b> {format_remaining(remaining)}</p>
      <p><b>Duration:</b> {h}h {m:02d}m ({duration} sec)</p>
      <p><b>Players:</b> {len(store.get('teams', {}))}</p>
    </div>
    <div class="card">
      <h2 style="margin-top:0">Timer Controls</h2>
      <form method="POST" action="/admin" style="margin-bottom:0.8rem">
        <input type="hidden" name="action" value="start">
        <label>Admin password</label>
        <input type="password" name="password" required>
        <button type="submit">▶ Start Event</button>
      </form>
      <form method="POST" action="/admin" style="margin-bottom:0.8rem">
        <input type="hidden" name="action" value="stop">
        <label>Admin password</label>
        <input type="password" name="password" required>
        <button type="submit">⏸ Stop / Freeze</button>
      </form>
      <form method="POST" action="/admin" style="margin-bottom:0.8rem">
        <input type="hidden" name="action" value="resume">
        <label>Admin password</label>
        <input type="password" name="password" required>
        <button type="submit">▶ Resume</button>
      </form>
      <form method="POST" action="/admin" style="margin-bottom:0.8rem">
        <input type="hidden" name="action" value="reset">
        <label>Admin password</label>
        <input type="password" name="password" required>
        <button type="submit">↺ Reset Timer (keep players)</button>
      </form>
    </div>
    <div class="card">
      <h2 style="margin-top:0">Set Duration</h2>
      <form method="POST" action="/admin">
        <input type="hidden" name="action" value="set_duration">
        <label>Admin password</label>
        <input type="password" name="password" required>
        <label>Hours</label>
        <input type="text" name="hours" value="{h}" placeholder="1">
        <label>Minutes</label>
        <input type="text" name="minutes" value="{m}" placeholder="30">
        <button type="submit">Set Duration</button>
      </form>
      <p class="hint">Default is 1 hour 30 minutes. Changing duration resets the clock to not-started.</p>
    </div>
    <div class="card">
      <h2 style="margin-top:0">Danger Zone</h2>
      <form method="POST" action="/admin">
        <input type="hidden" name="action" value="wipe">
        <label>Admin password</label>
        <input type="password" name="password" required>
        <button type="submit">Delete all players &amp; reset</button>
      </form>
    </div>
    <p><a href="/">← Home</a> · <a href="/scoreboard">Scoreboard</a></p>
    """
    return page("Admin", body)


class CTFHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

    def get_team_cookie(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("team="):
                return urllib.parse.unquote(part[5:])
        return None

    def set_team_cookie(self, team):
        return f"team={urllib.parse.quote(team)}; Path=/; HttpOnly; SameSite=Lax"

    def send_html(self, content, code=200, extra_headers=None):
        data = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path, content_type="application/octet-stream"):
        if not os.path.isfile(path):
            self.send_html(page("404", "<h1>File not found</h1>"), 404)
            return
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, loc, extra_headers=None):
        self.send_response(302)
        self.send_header("Location", loc)
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        team = self.get_team_cookie()

        if path == "/" or path == "/index.html":
            self.send_html(home_page(team))
        elif path == "/challenges":
            self.send_html(challenges_page(team))
        elif path.startswith("/challenge/"):
            cid = path.split("/")[-1]
            self.send_html(challenge_page(cid, team))
        elif path == "/scoreboard":
            self.send_html(scoreboard_page(team))
        elif path == "/register":
            self.send_html(register_page(team=team))
        elif path == "/login":
            self.send_html(login_page(team=team))
        elif path == "/admin":
            self.send_html(admin_page())
        elif path.startswith("/download/"):
            fname = path.split("/")[-1]
            # only allow known challenge files
            allowed = {c["file"] for c in CHALLENGES.values() if c.get("file")}
            if fname in allowed:
                fpath = os.path.join(STATIC_DIR, fname)
                ctype = "image/png" if fname.endswith(".png") else "application/octet-stream"
                self.send_file(fpath, ctype)
            else:
                self.send_html(page("404", "<h1>Not found</h1>"), 404)
        elif path == "/static/notice.png" or path == "/notice.png":
            self.send_file(os.path.join(STATIC_DIR, "notice.png"), "image/png")
        else:
            self.send_html(page("404", "<h1>404 — Page not found</h1><p><a href='/'>Home</a></p>"), 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        form = urllib.parse.parse_qs(raw)
        path = urllib.parse.urlparse(self.path).path
        team_cookie = self.get_team_cookie()

        def get(key, default=""):
            return form.get(key, [default])[0].strip()

        # --- Register ---
        if path == "/register":
            name = get("team")
            pw = get("password")
            if not name or len(name) < 2:
                self.send_html(register_page(("err", "Name too short.")))
                return
            if len(name) > 32:
                self.send_html(register_page(("err", "Name too long.")))
                return
            with lock:
                if name in store["teams"]:
                    # allow reclaim with password
                    existing = store["teams"][name]
                    if pw and existing.get("pw") == hash_pw(pw):
                        pass  # reclaim ok
                    elif existing.get("pw") and (not pw or existing.get("pw") != hash_pw(pw)):
                        self.send_html(register_page(("err", "Name already taken.")))
                        return
                store["teams"][name] = store["teams"].get(name, {
                    "pw": hash_pw(pw) if pw else "",
                    "solves": {},
                })
                if pw:
                    store["teams"][name]["pw"] = hash_pw(pw)
                save_store()
            self.redirect("/challenges", {"Set-Cookie": self.set_team_cookie(name)})
            return

        # --- Submit flag ---
        if path.startswith("/submit/"):
            cid = path.split("/")[-1]
            flag = get("flag")
            team = get("team") or team_cookie
            remaining, frozen, status = event_remaining()
            if frozen or status != "RUNNING":
                msg = "Event has not started yet. Wait for admin." if status == "WAITING" else "Event time is over. Scoreboard is frozen."
                self.send_html(challenge_page(cid, team, ("err", msg)))
                return
            if cid not in CHALLENGES:
                self.send_html(challenges_page(team, ("err", "Invalid challenge.")))
                return
            if not team or team not in store["teams"]:
                self.send_html(challenge_page(cid, None, ("err", "Register first.")))
                return
            expected = CHALLENGES[cid]["flag"]
            with lock:
                if cid in store["teams"][team].get("solves", {}):
                    self.send_html(challenge_page(cid, team, ("info", "Already solved.")))
                    return
                if flag.strip() == expected:
                    store["teams"][team].setdefault("solves", {})[cid] = time.time()
                    store["submissions"].append({
                        "team": team, "chal": cid, "time": time.time(), "ok": True
                    })
                    save_store()
                    self.send_html(challenge_page(cid, team, ("ok", f"Correct! +{CHALLENGES[cid]['points']} points")))
                else:
                    store["submissions"].append({
                        "team": team, "chal": cid, "time": time.time(), "ok": False
                    })
                    save_store()
                    self.send_html(challenge_page(cid, team, ("err", "Wrong flag. Try again.")))
            return

        # --- Admin controls ---
        if path == "/admin":
            password = get("password")
            action = get("action")
            if password != ADMIN_PASSWORD:
                self.send_html(admin_page(("err", "Wrong admin password.")))
                return
            with lock:
                if action == "start":
                    store["running"] = True
                    store["frozen"] = False
                    store["start_time"] = time.time()
                    save_store()
                    self.send_html(admin_page(("ok", "Event STARTED. Clock is running.")))
                elif action == "stop":
                    store["frozen"] = True
                    store["running"] = False
                    # preserve remaining by shifting start_time
                    if store.get("start_time"):
                        elapsed = time.time() - store["start_time"]
                        left = max(0, store.get("duration_sec", EVENT_DURATION_SEC) - elapsed)
                        store["duration_sec"] = int(left) if left > 0 else store.get("duration_sec", EVENT_DURATION_SEC)
                        # keep start cleared so resume can set new start with remaining as duration
                        store["_paused_remaining"] = int(left)
                    save_store()
                    self.send_html(admin_page(("ok", "Event STOPPED / frozen.")))
                elif action == "resume":
                    left = store.get("_paused_remaining")
                    if left is not None:
                        store["duration_sec"] = int(left)
                    store["running"] = True
                    store["frozen"] = False
                    store["start_time"] = time.time()
                    store.pop("_paused_remaining", None)
                    save_store()
                    self.send_html(admin_page(("ok", "Event RESUMED.")))
                elif action == "reset":
                    store["running"] = False
                    store["frozen"] = False
                    store["start_time"] = None
                    store["duration_sec"] = EVENT_DURATION_SEC
                    store.pop("_paused_remaining", None)
                    save_store()
                    self.send_html(admin_page(("ok", "Timer reset. Press Start when ready.")))
                elif action == "set_duration":
                    try:
                        hours = int(get("hours") or "0")
                        minutes = int(get("minutes") or "0")
                        secs = max(60, hours * 3600 + minutes * 60)  # min 1 minute
                    except ValueError:
                        self.send_html(admin_page(("err", "Invalid hours/minutes.")))
                        return
                    store["duration_sec"] = secs
                    store["running"] = False
                    store["frozen"] = False
                    store["start_time"] = None
                    store.pop("_paused_remaining", None)
                    save_store()
                    self.send_html(admin_page(("ok", f"Duration set to {secs // 3600}h {(secs % 3600) // 60}m. Press Start when ready.")))
                elif action == "wipe":
                    store["teams"] = {}
                    store["submissions"] = []
                    store["running"] = False
                    store["frozen"] = False
                    store["start_time"] = None
                    store["duration_sec"] = EVENT_DURATION_SEC
                    store.pop("_paused_remaining", None)
                    save_store()
                    self.send_html(admin_page(("ok", "All players wiped. Timer reset.")))
                else:
                    self.send_html(admin_page(("err", "Unknown action.")))
            return

        # --- Login (Round 3 SQLi) ---
        if path == "/login":
            user = get("user")
            password = get("pass")
            if fake_sql_login(user, password):
                self.send_html(login_page(team=team_cookie, show_flag=True))
            else:
                self.send_html(login_page(("err", "Login failed"), team=team_cookie))
            return

        self.send_html(page("Error", "<h1>Unknown action</h1>"), 400)


def ensure_notice_png():
    """Create a minimal valid PNG if steghide not available. Embed flag in text chunk for demo."""
    path = os.path.join(STATIC_DIR, "notice.png")
    if os.path.exists(path) and os.path.getsize(path) > 100:
        return

    # Minimal 1x1 PNG + tEXt chunk with instruction (real stego needs steghide on admin side)
    # For the event, admin should replace this with a real steghide image.
    # We still ship a valid PNG so download works; flag is only via steghide password "college".
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)

    # 64x64 solid dark blue PNG
    width, height = 64, 64
    raw = b""
    for y in range(height):
        raw += b"\x00"  # filter none
        for x in range(width):
            raw += bytes([15, 23, 42, 255])  # dark blue RGBA

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", ihdr)
    # Visible hint in tEXt (not the flag)
    png += chunk(b"tEXt", b"Comment\x00Use steghide with password: college")
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")

    os.makedirs(STATIC_DIR, exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)
    print(f"[+] Created placeholder notice.png at {path}")
    print("[!] IMPORTANT: Replace notice.png with a real steghide-embedded image before the event.")
    print("    Example: steghide embed -cf notice.png -ef flag.txt -p college")


def main():
    load_store()
    # migrate old data.json
    if "start_time" not in store:
        store["start_time"] = None
    if "frozen" not in store:
        store["frozen"] = False
    if "running" not in store:
        store["running"] = False
    if "duration_sec" not in store:
        store["duration_sec"] = EVENT_DURATION_SEC
    # Do NOT auto-start — admin starts from /admin
    ensure_notice_png()
    os.makedirs(STATIC_DIR, exist_ok=True)

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((HOST, PORT), CTFHandler) as httpd:
        print("=" * 56)
        print("  College CTF — 1.5 Hour Event  |  RUNNING")
        print("=" * 56)
        print(f"  Local:    http://127.0.0.1:{PORT}")
        print(f"  Network:  http://0.0.0.0:{PORT}")
        print()
        print("  Pages:")
        print(f"    Home         http://127.0.0.1:{PORT}/")
        print(f"    Challenges   http://127.0.0.1:{PORT}/challenges")
        print(f"    Scoreboard   http://127.0.0.1:{PORT}/scoreboard")
        print(f"    Register     http://127.0.0.1:{PORT}/register")
        print(f"    Login (R3)   http://127.0.0.1:{PORT}/login")
        print()
        print("  Flags:")
        for c in CHALLENGES.values():
            print(f"    [{c['difficulty']}] {c['name']}: {c['flag']}")
        print("=" * 56)
        print("  Press Ctrl+C to stop")
        print("=" * 56)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[+] Shutting down.")
            save_store()


if __name__ == "__main__":
    main()
