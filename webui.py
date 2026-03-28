#!/usr/bin/env python3
"""TedBot Web UI — Notion-minimal, mobile-first, with tags, soft delete, search"""

import os
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request, render_template_string

DB_PATH = os.environ.get("DB_PATH", os.path.expanduser("~/.tedbot/tedbot.db"))
app = Flask(__name__)

CATEGORIES = ["grocery", "baby", "work", "health", "finance", "general"]

CATEGORY_TAGS = {
    "baby":    ["weight", "growth", "poop", "pee", "feed", "activity"],
    "grocery": ["costco", "local", "walmart", "butcher"],
}

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

# ── Notes API ─────────────────────────────────────────────────────────────────
@app.route("/api/notes", methods=["GET"])
def get_notes():
    category = request.args.get("category")
    query    = request.args.get("q", "").strip()
    con = db()
    sql  = "SELECT * FROM notes WHERE archived_at IS NULL"
    args = []
    if category:
        sql += " AND category=?"; args.append(category)
    if query:
        like = f"%{query}%"
        sql += " AND (LOWER(content) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(category) LIKE ?)"
        args += [like, like, like]
    sql += " ORDER BY created_at DESC"
    rows = con.execute(sql, args).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/notes", methods=["POST"])
def create_note():
    data = request.json
    con = db()
    cur = con.execute(
        "INSERT INTO notes (category, content, tags, created_at) VALUES (?, ?, ?, ?)",
        (data.get("category", "general"), data["content"],
         data.get("tags", ""), datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    con.commit(); nid = cur.lastrowid; con.close()
    return jsonify({"id": nid, "ok": True})

@app.route("/api/notes/<int:nid>", methods=["PUT"])
def update_note(nid):
    data = request.json
    con = db()
    con.execute("UPDATE notes SET category=?, content=?, tags=? WHERE id=?",
                (data["category"], data["content"], data.get("tags", ""), nid))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/notes/<int:nid>", methods=["DELETE"])
def archive_note(nid):
    con = db()
    con.execute("UPDATE notes SET archived_at=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M"), nid))
    con.commit(); con.close()
    return jsonify({"ok": True})

# ── Reminders API ─────────────────────────────────────────────────────────────
@app.route("/api/reminders", methods=["GET"])
def get_reminders():
    con = db()
    # Return all non-archived reminders — overdue ones included
    rows = con.execute(
        "SELECT * FROM reminders WHERE archived_at IS NULL ORDER BY remind_at ASC"
    ).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/reminders", methods=["POST"])
def create_reminder():
    data = request.json
    con = db()
    cur = con.execute(
        "INSERT INTO reminders (content, remind_at, created_at) VALUES (?, ?, ?)",
        (data["content"], data["remind_at"], datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    con.commit(); rid = cur.lastrowid; con.close()
    return jsonify({"id": rid, "ok": True})

@app.route("/api/reminders/<int:rid>", methods=["PUT"])
def update_reminder(rid):
    data = request.json
    con = db()
    con.execute("UPDATE reminders SET content=?, remind_at=? WHERE id=?",
                (data["content"], data["remind_at"], rid))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/reminders/<int:rid>", methods=["DELETE"])
def archive_reminder(rid):
    """Dismiss = soft delete. Data stays forever."""
    con = db()
    con.execute("UPDATE reminders SET archived_at=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M"), rid))
    con.commit(); con.close()
    return jsonify({"ok": True})

# ── Search API ────────────────────────────────────────────────────────────────
@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"notes": [], "reminders": []})
    like = f"%{q.lower()}%"
    con = db()
    notes = con.execute(
        "SELECT * FROM notes WHERE archived_at IS NULL "
        "AND (LOWER(content) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(category) LIKE ?) "
        "ORDER BY created_at DESC LIMIT 30",
        (like, like, like)
    ).fetchall()
    reminders = con.execute(
        "SELECT * FROM reminders WHERE archived_at IS NULL "
        "AND LOWER(content) LIKE ? ORDER BY remind_at ASC LIMIT 20",
        (like,)
    ).fetchall()
    con.close()
    return jsonify({"notes": [dict(r) for r in notes], "reminders": [dict(r) for r in reminders]})

# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ted's Workspace</title>
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,500;1,400&family=JetBrains+Mono:wght@400;500&family=Karla:wght@300;400;500&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #f7f6f3;
  --surface: #ffffff;
  --border: #e8e7e3;
  --border2: #d4d3ce;
  --text: #1a1a18;
  --muted: #9b9a97;
  --muted2: #c4c3be;
  --accent: #2e6fdb;
  --amber: #d97706;
  --amber-bg: #fffbeb;
  --amber-border: #fcd34d;
  --hover: #f1f0ec;
  --sidebar-w: 240px;
  --topbar-h: 52px;
  --sans: 'Karla', sans-serif;
  --serif: 'Lora', Georgia, serif;
  --mono: 'JetBrains Mono', monospace;
}

body { background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 15px; line-height: 1.6; }

/* ── Sidebar ── */
.sidebar {
  position: fixed; top: 0; left: 0; bottom: 0;
  width: var(--sidebar-w);
  background: #fbfaf8;
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
  padding: 24px 0;
  z-index: 200;
  transition: transform 0.25s cubic-bezier(.4,0,.2,1);
}
.sidebar-logo { padding: 0 20px 20px; border-bottom: 1px solid var(--border); margin-bottom: 8px; }
.sidebar-logo h1 { font-family: var(--serif); font-size: 1.1rem; font-weight: 500; letter-spacing: -0.01em; }
.sidebar-logo .sub { font-size: 0.68rem; color: var(--muted); font-family: var(--mono); margin-top: 2px; }
.sidebar-section { padding: 8px 12px 4px; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); font-family: var(--mono); }
.sidebar-item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 20px; cursor: pointer;
  color: var(--muted); font-size: 0.88rem; font-weight: 400;
  border: none; background: none; width: 100%; text-align: left;
  transition: background 0.1s, color 0.1s;
}
.sidebar-item:hover { background: var(--hover); color: var(--text); }
.sidebar-item.active { background: var(--hover); color: var(--text); font-weight: 500; }
.sidebar-item .emoji { font-size: 0.85rem; width: 18px; }
.sidebar-item .count { margin-left: auto; font-family: var(--mono); font-size: 0.65rem; color: var(--muted2); }
.sidebar-overlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.3); z-index: 199;
}
.sidebar-overlay.open { display: block; }

/* ── Main ── */
.main { margin-left: var(--sidebar-w); min-height: 100vh; display: flex; flex-direction: column; }

/* ── Topbar ── */
.topbar {
  position: sticky; top: 0; z-index: 100;
  height: var(--topbar-h);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center;
  padding: 0 32px; gap: 12px;
}
.burger {
  display: none; background: none; border: none; cursor: pointer;
  padding: 6px; border-radius: 6px;
  color: var(--text); font-size: 1.2rem; line-height: 1;
  transition: background 0.1s;
}
.burger:hover { background: var(--hover); }
.page-title { font-family: var(--serif); font-size: 1rem; font-weight: 500; }
.topbar-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }

/* Search bar */
.search-wrap {
  position: relative; display: flex; align-items: center;
}
.search-input {
  border: 1px solid var(--border2); border-radius: 6px;
  padding: 5px 10px 5px 28px;
  font-family: var(--sans); font-size: 0.82rem;
  color: var(--text); background: var(--bg);
  outline: none; width: 180px;
  transition: border-color 0.15s, width 0.2s;
}
.search-input:focus { border-color: var(--accent); width: 220px; }
.search-input::placeholder { color: var(--muted2); }
.search-icon {
  position: absolute; left: 8px;
  color: var(--muted2); font-size: 0.8rem; pointer-events: none;
}
.search-results {
  position: absolute; top: calc(100% + 6px); right: 0;
  width: 320px; background: var(--surface);
  border: 1px solid var(--border); border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.1);
  display: none; z-index: 300; max-height: 420px; overflow-y: auto;
}
.search-results.open { display: block; }
.sr-section { padding: 8px 14px 4px; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); font-family: var(--mono); border-top: 1px solid var(--border); }
.sr-section:first-child { border-top: none; }
.sr-row {
  padding: 8px 14px; font-size: 0.85rem; cursor: pointer;
  transition: background 0.1s;
  display: flex; align-items: flex-start; gap: 8px;
}
.sr-row:hover { background: var(--hover); }
.sr-cat { font-family: var(--mono); font-size: 0.62rem; color: var(--muted); white-space: nowrap; margin-top: 3px; }
.sr-empty { padding: 16px 14px; color: var(--muted2); font-size: 0.82rem; font-family: var(--mono); }

/* ── Content ── */
.content { padding: 36px 48px; max-width: 860px; }

/* Inline add */
.inline-add {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 0; border-bottom: 1px solid var(--border);
  margin-bottom: 24px; flex-wrap: wrap;
}
.inline-add input, .inline-add select {
  border: none; background: transparent;
  font-family: var(--sans); font-size: 0.93rem; color: var(--text);
  outline: none; min-width: 0;
}
.inline-add .ia-text { flex: 1; min-width: 160px; }
.inline-add input::placeholder { color: var(--muted2); }
.inline-add select { color: var(--muted); font-size: 0.8rem; cursor: pointer; }
.inline-add .ia-date { font-size: 0.8rem; color: var(--muted); }
.add-btn {
  background: var(--text); color: white; border: none;
  border-radius: 6px; padding: 5px 13px;
  font-family: var(--sans); font-size: 0.8rem;
  cursor: pointer; opacity: 0; transition: opacity 0.15s; white-space: nowrap;
}
.inline-add:focus-within .add-btn { opacity: 1; }
.add-btn:hover { background: #333; }

/* Tag suggestion pills (shown in add bar) */
.tag-suggestions { display: flex; gap: 5px; flex-wrap: wrap; padding: 4px 0; }
.tag-suggest-pill {
  font-family: var(--mono); font-size: 0.62rem;
  padding: 2px 8px; border-radius: 10px;
  background: var(--hover); color: var(--muted2);
  border: 1px solid var(--border2); cursor: pointer;
  transition: all 0.12s; user-select: none;
}
.tag-suggest-pill:hover { color: var(--text); border-color: var(--border2); background: var(--border); }
.tag-suggest-pill.selected { background: var(--text); color: white; border-color: var(--text); }

/* Note rows */
.note-row {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 8px 0; border-bottom: 1px solid var(--border);
  transition: background 0.1s; position: relative;
}
.note-row:hover { background: var(--hover); margin: 0 -48px; padding-left: 48px; padding-right: 48px; }
.note-check {
  width: 15px; height: 15px; border: 1.5px solid var(--border2);
  border-radius: 3px; margin-top: 4px; flex-shrink: 0;
  cursor: pointer; transition: all 0.15s;
}
.note-check:hover { border-color: #e03e3e; background: #fee2e2; }
.note-body { flex: 1; min-width: 0; }
.note-text {
  width: 100%; font-size: 0.92rem; line-height: 1.55;
  outline: none; border: none; background: transparent;
  font-family: var(--sans); color: var(--text);
  resize: none; padding: 0; min-height: 22px;
}
.note-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
.cat-tag {
  font-family: var(--mono); font-size: 0.63rem;
  padding: 2px 7px; border-radius: 3px;
  background: var(--hover); color: var(--muted);
  white-space: nowrap; cursor: pointer;
}
.tag-pill {
  font-family: var(--mono); font-size: 0.60rem;
  padding: 1px 6px; border-radius: 10px;
  background: transparent; color: var(--muted2);
  border: 1px solid var(--border2); white-space: nowrap;
}
.row-date { font-family: var(--mono); font-size: 0.63rem; color: var(--muted2); white-space: nowrap; margin-left: auto; }
.del-btn {
  opacity: 0; border: none; background: none; color: var(--muted);
  cursor: pointer; font-size: 0.78rem; padding: 2px 5px;
  border-radius: 3px; transition: all 0.1s; margin-top: 2px; flex-shrink: 0;
}
.note-row:hover .del-btn { opacity: 1; }
.del-btn:hover { background: #fee2e2; color: #e03e3e; }

/* Reminder rows */
.reminder-row {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 0; border-bottom: 1px solid var(--border);
  transition: background 0.1s;
}
.reminder-row:hover { background: var(--hover); margin: 0 -48px; padding-left: 48px; padding-right: 48px; }
.r-check {
  width: 15px; height: 15px; border: 1.5px solid var(--border2);
  border-radius: 50%; flex-shrink: 0; cursor: pointer; transition: all 0.15s;
}
.r-check:hover { border-color: #0f7b6c; background: #dcfce7; }
.r-text { flex: 1; font-size: 0.92rem; }
.r-date { font-family: var(--mono); font-size: 0.65rem; color: var(--muted); white-space: nowrap; }
.r-fired { font-family: var(--mono); font-size: 0.58rem; color: var(--muted2); white-space: nowrap; }
.reminder-row:hover .del-btn { opacity: 1; }

/* Overdue reminders — amber tint */
.reminder-row.overdue {
  background: var(--amber-bg);
  opacity: 0.85;
}
.reminder-row.overdue:hover {
  background: #fef3c7;
  margin: 0 -48px; padding-left: 48px; padding-right: 48px;
}
.reminder-row.overdue .r-date { color: var(--amber); font-weight: 500; }
.reminder-row.overdue .r-check { border-color: var(--amber-border); }
.reminder-row.overdue .r-check:hover { border-color: var(--amber); background: #fef3c7; }

/* Calendar */
.cal-header { display: flex; align-items: center; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
.cal-header h2 { font-family: var(--serif); font-size: 1.05rem; font-weight: 500; }
.cal-nav {
  border: 1px solid var(--border); background: var(--surface);
  border-radius: 6px; padding: 4px 12px;
  font-family: var(--sans); font-size: 0.8rem;
  cursor: pointer; color: var(--text); transition: background 0.1s;
}
.cal-nav:hover { background: var(--hover); }
.cal-grid {
  display: grid; grid-template-columns: repeat(7, 1fr);
  gap: 1px; background: var(--border);
  border: 1px solid var(--border); border-radius: 8px;
  overflow: hidden; margin-bottom: 28px;
}
.cal-day-hdr {
  background: #fbfaf8; text-align: center; padding: 7px 0;
  font-family: var(--mono); font-size: 0.62rem;
  text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted);
}
.cal-cell { background: var(--surface); min-height: 64px; padding: 5px 6px; }
.cal-cell.other { background: #fbfaf8; }
.cal-cell.other .day-n { color: var(--muted2); }
.cal-cell.today { background: #fffbeb; }
.cal-cell.today .day-n { color: var(--accent); font-weight: 600; }
.day-n { font-family: var(--mono); font-size: 0.68rem; color: var(--muted); margin-bottom: 3px; }
.cal-pill {
  font-size: 0.6rem; background: var(--text); color: white;
  border-radius: 3px; padding: 1px 4px; margin-bottom: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  cursor: pointer; display: block;
}
.cal-pill:hover { background: var(--accent); }
.cal-pill.overdue { background: var(--amber); }

/* Buttons */
.btn { border: 1px solid var(--border2); background: var(--surface); color: var(--text); font-family: var(--sans); font-size: 0.82rem; padding: 6px 14px; border-radius: 6px; cursor: pointer; transition: background 0.1s; }
.btn:hover { background: var(--hover); }
.btn.primary { background: var(--text); color: white; border-color: var(--text); }
.btn.primary:hover { background: #333; }

/* Modal */
.overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.3); z-index:300; align-items:center; justify-content:center; padding: 20px; }
.overlay.open { display:flex; }
.modal { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 24px; width: 100%; max-width: 420px; box-shadow: 0 20px 60px rgba(0,0,0,0.12); }
.modal h3 { font-family: var(--serif); font-size: 1.05rem; font-weight: 500; margin-bottom: 16px; }
.field { display:flex; flex-direction:column; gap:4px; margin-bottom:12px; }
.field label { font-size:0.68rem; font-family:var(--mono); color:var(--muted); text-transform:uppercase; letter-spacing:0.06em; }
.field input, .field select, .field textarea { border: 1px solid var(--border2); border-radius: 6px; padding: 8px 10px; font-family: var(--sans); font-size: 0.88rem; color: var(--text); background: var(--bg); outline: none; }
.field input:focus, .field select:focus, .field textarea:focus { border-color: var(--accent); }
.field textarea { resize: vertical; min-height: 70px; }
.modal-footer { display:flex; gap:8px; justify-content:flex-end; margin-top:16px; }
.modal-tag-row { display:flex; gap:5px; flex-wrap:wrap; margin-top:6px; }

.empty { padding: 28px 0; color: var(--muted2); font-size: 0.85rem; font-family: var(--mono); }
.section-hint { font-size: 0.78rem; color: var(--muted); margin-bottom: 20px; }

/* ── Mobile ── */
@media (max-width: 680px) {
  .sidebar { transform: translateX(-100%); }
  .sidebar.open { transform: translateX(0); box-shadow: 4px 0 24px rgba(0,0,0,0.12); }
  .main { margin-left: 0; }
  .burger { display: flex; align-items: center; justify-content: center; }
  .content { padding: 20px 16px; }
  .topbar { padding: 0 12px; gap: 8px; }
  .search-input { width: 130px; }
  .search-input:focus { width: 160px; }
  .search-results { width: 280px; right: -10px; }
  .note-row:hover { margin: 0 -16px; padding-left: 16px; padding-right: 16px; }
  .reminder-row:hover { margin: 0 -16px; padding-left: 16px; padding-right: 16px; }
  .reminder-row.overdue:hover { margin: 0 -16px; padding-left: 16px; padding-right: 16px; }
}
</style>
</head>
<body>

<div class="sidebar-overlay" id="overlay" onclick="closeSidebar()"></div>

<nav class="sidebar" id="sidebar">
  <div class="sidebar-logo">
    <h1>Ted's Workspace</h1>
    <div class="sub">// personal os</div>
  </div>
  <div class="sidebar-section">Views</div>
  <button class="sidebar-item active" onclick="showView('all')" id="nav-all">
    <span class="emoji">📋</span> All Notes <span class="count" id="cnt-all"></span>
  </button>
  <button class="sidebar-item" onclick="showView('calendar')" id="nav-calendar">
    <span class="emoji">📅</span> Calendar
  </button>
  <div class="sidebar-section" style="margin-top:10px;">Categories</div>
  <button class="sidebar-item" onclick="showView('grocery')" id="nav-grocery"><span class="emoji">🛒</span> Grocery <span class="count" id="cnt-grocery"></span></button>
  <button class="sidebar-item" onclick="showView('baby')" id="nav-baby"><span class="emoji">🍼</span> Baby <span class="count" id="cnt-baby"></span></button>
  <button class="sidebar-item" onclick="showView('work')" id="nav-work"><span class="emoji">💼</span> Work <span class="count" id="cnt-work"></span></button>
  <button class="sidebar-item" onclick="showView('health')" id="nav-health"><span class="emoji">❤️</span> Health <span class="count" id="cnt-health"></span></button>
  <button class="sidebar-item" onclick="showView('finance')" id="nav-finance"><span class="emoji">💰</span> Finance <span class="count" id="cnt-finance"></span></button>
  <button class="sidebar-item" onclick="showView('general')" id="nav-general"><span class="emoji">📝</span> General <span class="count" id="cnt-general"></span></button>
</nav>

<div class="main">
  <div class="topbar">
    <button class="burger" onclick="toggleSidebar()" aria-label="Menu">☰</button>
    <span class="page-title" id="page-title">All Notes</span>
    <div class="topbar-actions">
      <div class="search-wrap">
        <span class="search-icon">⌕</span>
        <input class="search-input" type="text" id="search-input" placeholder="Search…"
               oninput="onSearch(this.value)" onfocus="onSearchFocus()" autocomplete="off">
        <div class="search-results" id="search-results"></div>
      </div>
      <button class="btn primary" onclick="openAdd()">+ New</button>
    </div>
  </div>

  <div class="content">

    <!-- Notes panel -->
    <div id="panel-notes">
      <div class="inline-add" id="quick-add-bar">
        <input class="ia-text" type="text" id="quick-note" placeholder="Write a note, press Enter…"
               onkeydown="if(event.key==='Enter'){event.preventDefault();quickAddNote();}"
               oninput="updateTagSuggestions()">
        <select id="quick-cat" onchange="updateTagSuggestions()">
          <option value="general">general</option>
          <option value="grocery">grocery</option>
          <option value="baby">baby</option>
          <option value="work">work</option>
          <option value="health">health</option>
          <option value="finance">finance</option>
        </select>
        <button class="add-btn" onclick="quickAddNote()">Save</button>
      </div>
      <div class="tag-suggestions" id="tag-suggestions"></div>
      <div id="notes-list"></div>
    </div>

    <!-- Calendar panel -->
    <div id="panel-calendar" style="display:none">
      <div class="inline-add">
        <input class="ia-text" type="text" id="quick-reminder" placeholder="Add reminder…"
               onkeydown="if(event.key==='Enter') quickAddReminder();">
        <input class="ia-date" type="datetime-local" id="quick-rdate">
        <button class="add-btn" onclick="quickAddReminder()">Save</button>
      </div>
      <div class="cal-header">
        <button class="cal-nav" onclick="prevMonth()">←</button>
        <h2 id="cal-title"></h2>
        <button class="cal-nav" onclick="nextMonth()">→</button>
        <button class="cal-nav" onclick="goToday()">Today</button>
      </div>
      <div class="cal-grid" id="cal-grid"></div>
      <p class="section-hint">All reminders — amber = overdue, waiting for dismissal</p>
      <div id="reminder-list"></div>
    </div>

  </div>
</div>

<!-- Edit note modal -->
<div class="overlay" id="edit-note-modal">
  <div class="modal">
    <h3>Edit Note</h3>
    <div class="field"><label>Category</label>
      <select id="en-cat" onchange="updateModalTags()">
        <option value="general">📝 General</option><option value="grocery">🛒 Grocery</option>
        <option value="baby">🍼 Baby</option><option value="work">💼 Work</option>
        <option value="health">❤️ Health</option><option value="finance">💰 Finance</option>
      </select>
    </div>
    <div class="field"><label>Content</label><textarea id="en-content" rows="4"></textarea></div>
    <div class="field">
      <label>Tags</label>
      <div class="modal-tag-row" id="modal-tag-row"></div>
      <input type="hidden" id="en-tags">
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal('edit-note-modal')">Cancel</button>
      <button class="btn primary" onclick="saveEditNote()">Save</button>
    </div>
  </div>
</div>

<!-- Edit reminder modal -->
<div class="overlay" id="edit-r-modal">
  <div class="modal">
    <h3>Edit Reminder</h3>
    <div class="field"><label>What</label><textarea id="er-content" rows="3"></textarea></div>
    <div class="field"><label>When</label><input type="datetime-local" id="er-date"></div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal('edit-r-modal')">Cancel</button>
      <button class="btn primary" onclick="saveEditReminder()">Save</button>
    </div>
  </div>
</div>

<script>
const CATS = ['grocery','baby','work','health','finance','general'];
const CAT_EMOJI = {grocery:'🛒',baby:'🍼',work:'💼',health:'❤️',finance:'💰',general:'📝'};
const CAT_TAGS = {baby:['weight','growth','poop','pee','feed','activity'], grocery:['costco','local','walmart','butcher']};

let notes=[], reminders=[], currentView='all', editNoteId=null, editReminderId=null;
let calYear=new Date().getFullYear(), calMonth=new Date().getMonth();
let selectedTags=[], searchTimer=null;

// ── Sidebar ────────────────────────────────────────────────────────────────
function toggleSidebar() {
  const s=document.getElementById('sidebar'), o=document.getElementById('overlay');
  const open=s.classList.toggle('open');
  o.classList.toggle('open',open);
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('overlay').classList.remove('open');
}

// ── Views ──────────────────────────────────────────────────────────────────
function showView(v) {
  currentView=v; closeSidebar();
  document.querySelectorAll('.sidebar-item').forEach(el=>el.classList.remove('active'));
  const navEl=document.getElementById('nav-'+v);
  if(navEl) navEl.classList.add('active');
  const titles={all:'All Notes',calendar:'Calendar',grocery:'Grocery',baby:'Baby',work:'Work',health:'Health',finance:'Finance',general:'General'};
  document.getElementById('page-title').textContent=titles[v]||v;
  const isCal=v==='calendar';
  document.getElementById('panel-notes').style.display=isCal?'none':'block';
  document.getElementById('panel-calendar').style.display=isCal?'block':'none';
  if(isCal){loadReminders();}else{loadNotes(v==='all'?null:v);}
}

// ── Tag suggestions (add bar) ──────────────────────────────────────────────
function updateTagSuggestions() {
  const cat=document.getElementById('quick-cat').value;
  const vocab=CAT_TAGS[cat]||[];
  const el=document.getElementById('tag-suggestions');
  if(!vocab.length){el.innerHTML='';selectedTags=[];return;}
  el.innerHTML=vocab.map(t=>`
    <span class="tag-suggest-pill ${selectedTags.includes(t)?'selected':''}"
          onclick="toggleTag('${t}')">${t}</span>`).join('');
}

function toggleTag(tag) {
  if(selectedTags.includes(tag)) selectedTags=selectedTags.filter(t=>t!==tag);
  else selectedTags.push(tag);
  updateTagSuggestions();
}

// ── Modal tags ─────────────────────────────────────────────────────────────
function updateModalTags(currentTags=[]) {
  const cat=document.getElementById('en-cat').value;
  const vocab=CAT_TAGS[cat]||[];
  const row=document.getElementById('modal-tag-row');
  const tagList=typeof currentTags==='string'?currentTags.split(',').filter(Boolean):currentTags;
  if(!vocab.length){row.innerHTML='<span style="font-size:0.75rem;color:var(--muted2)">No tag suggestions for this category</span>';return;}
  row.innerHTML=vocab.map(t=>`
    <span class="tag-suggest-pill ${tagList.includes(t)?'selected':''}"
          onclick="toggleModalTag('${t}')">${t}</span>`).join('');
  document.getElementById('en-tags').value=tagList.join(',');
}

function toggleModalTag(tag) {
  const input=document.getElementById('en-tags');
  let tags=input.value.split(',').filter(Boolean);
  if(tags.includes(tag)) tags=tags.filter(t=>t!==tag); else tags.push(tag);
  input.value=tags.join(',');
  updateModalTags(tags);
}

// ── Notes ──────────────────────────────────────────────────────────────────
async function loadNotes(cat) {
  const url=cat?`/api/notes?category=${cat}`:'/api/notes';
  notes=await fetch(url).then(r=>r.json());
  renderNotes(); updateCounts();
}

function renderNotes() {
  const el=document.getElementById('notes-list');
  if(!notes.length){el.innerHTML='<div class="empty">No notes yet. Start typing above.</div>';return;}
  el.innerHTML=notes.map(n=>{
    const tagPills=(n.tags||'').split(',').filter(Boolean).map(t=>`<span class="tag-pill">${t}</span>`).join('');
    return `<div class="note-row">
      <div class="note-check" onclick="archiveNote(${n.id})" title="Archive"></div>
      <div class="note-body">
        <textarea class="note-text" onblur="saveNoteInline(${n.id},this.value,'${n.category}','${n.tags||''}')" oninput="autoResize(this)" rows="1">${esc(n.content)}</textarea>
        <div class="note-meta">
          <span class="cat-tag" onclick="openEditNote(${n.id})">${CAT_EMOJI[n.category]||''} ${n.category}</span>
          ${tagPills}
          <span class="row-date">${n.created_at.substring(0,10)}</span>
        </div>
      </div>
      <button class="del-btn" onclick="archiveNote(${n.id})" title="Archive">✕</button>
    </div>`;
  }).join('');
  document.querySelectorAll('.note-text').forEach(autoResize);
}

function autoResize(el){el.style.height='auto';el.style.height=el.scrollHeight+'px';}

async function saveNoteInline(id,content,category,tags) {
  if(!content.trim()) return;
  await fetch(`/api/notes/${id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({category,content,tags})});
}

async function quickAddNote() {
  const content=document.getElementById('quick-note').value.trim();
  const category=document.getElementById('quick-cat').value;
  if(!content) return;
  const tags=selectedTags.join(',');
  await fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category,content,tags})});
  document.getElementById('quick-note').value='';
  selectedTags=[]; updateTagSuggestions();
  loadNotes(currentView==='all'?null:currentView);
}

function openEditNote(id) {
  editNoteId=id;
  const n=notes.find(x=>x.id===id);
  document.getElementById('en-cat').value=n.category;
  document.getElementById('en-content').value=n.content;
  document.getElementById('en-tags').value=n.tags||'';
  updateModalTags((n.tags||'').split(',').filter(Boolean));
  document.getElementById('edit-note-modal').classList.add('open');
}

async function saveEditNote() {
  const cat=document.getElementById('en-cat').value;
  const content=document.getElementById('en-content').value.trim();
  const tags=document.getElementById('en-tags').value;
  if(!content) return;
  await fetch(`/api/notes/${editNoteId}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({category:cat,content,tags})});
  closeModal('edit-note-modal'); loadNotes(currentView==='all'?null:currentView);
}

async function archiveNote(id) {
  await fetch(`/api/notes/${id}`,{method:'DELETE'});
  loadNotes(currentView==='all'?null:currentView);
}

// ── Reminders ──────────────────────────────────────────────────────────────
async function loadReminders() {
  reminders=await fetch('/api/reminders').then(r=>r.json());
  renderReminderList(); renderCalendar();
}

function renderReminderList() {
  const el=document.getElementById('reminder-list');
  if(!reminders.length){el.innerHTML='<div class="empty">No reminders.</div>';return;}
  const now=new Date();
  el.innerHTML=reminders.map(r=>{
    const dt=new Date(r.remind_at);
    const overdue=dt<now?'overdue':'';
    const firedHint=r.fired_at?`<span class="r-fired">notified ${fmtDate(r.fired_at)}</span>`:'';
    return `<div class="reminder-row ${overdue}">
      <div class="r-check" onclick="dismissReminder(${r.id})" title="Dismiss"></div>
      <div style="flex:1;min-width:0">
        <div class="r-text">${esc(r.content)}</div>
        ${firedHint}
      </div>
      <span class="r-date">${fmtDate(r.remind_at)}</span>
      <button class="del-btn" onclick="openEditReminder(${r.id})" style="opacity:1">✎</button>
      <button class="del-btn" onclick="dismissReminder(${r.id})">✕</button>
    </div>`;
  }).join('');
}

async function quickAddReminder() {
  const content=document.getElementById('quick-reminder').value.trim();
  const dt=document.getElementById('quick-rdate').value;
  if(!content||!dt){alert('Please fill in both the reminder and date/time.');return;}
  await fetch('/api/reminders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,remind_at:dt.replace('T',' ')})});
  document.getElementById('quick-reminder').value='';
  document.getElementById('quick-rdate').value='';
  loadReminders();
}

function openEditReminder(id) {
  editReminderId=id;
  const r=reminders.find(x=>x.id===id);
  document.getElementById('er-content').value=r.content;
  document.getElementById('er-date').value=r.remind_at.replace(' ','T');
  document.getElementById('edit-r-modal').classList.add('open');
}

async function saveEditReminder() {
  const content=document.getElementById('er-content').value.trim();
  const remind_at=document.getElementById('er-date').value.replace('T',' ');
  await fetch(`/api/reminders/${editReminderId}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,remind_at})});
  closeModal('edit-r-modal'); loadReminders();
}

async function dismissReminder(id) {
  // Soft delete — data stays in DB, just archived
  await fetch(`/api/reminders/${id}`,{method:'DELETE'});
  loadReminders();
}

// ── Calendar ───────────────────────────────────────────────────────────────
function renderCalendar() {
  const months=['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('cal-title').textContent=`${months[calMonth]} ${calYear}`;
  const days=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  let html=days.map(d=>`<div class="cal-day-hdr">${d}</div>`).join('');
  const first=new Date(calYear,calMonth,1).getDay();
  const dim=new Date(calYear,calMonth+1,0).getDate();
  const dipm=new Date(calYear,calMonth,0).getDate();
  const today=new Date();
  const now=new Date();
  for(let i=first-1;i>=0;i--) html+=`<div class="cal-cell other"><div class="day-n">${dipm-i}</div></div>`;
  for(let d=1;d<=dim;d++){
    const isToday=today.getFullYear()===calYear&&today.getMonth()===calMonth&&today.getDate()===d;
    const ds=`${calYear}-${String(calMonth+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const dayR=reminders.filter(r=>r.remind_at&&r.remind_at.startsWith(ds));
    const pills=dayR.map(r=>{
      const isOverdue=new Date(r.remind_at)<now;
      return `<span class="cal-pill ${isOverdue?'overdue':''}" onclick="openEditReminder(${r.id})" title="${esc(r.content)}">${esc(r.content.substring(0,16))}</span>`;
    }).join('');
    html+=`<div class="cal-cell${isToday?' today':''}"><div class="day-n">${d}</div>${pills}</div>`;
  }
  const total=Math.ceil((first+dim)/7)*7;
  for(let d=1;d<=total-first-dim;d++) html+=`<div class="cal-cell other"><div class="day-n">${d}</div></div>`;
  document.getElementById('cal-grid').innerHTML=html;
}
function prevMonth(){calMonth--;if(calMonth<0){calMonth=11;calYear--;}renderCalendar();}
function nextMonth(){calMonth++;if(calMonth>11){calMonth=0;calYear++;}renderCalendar();}
function goToday(){calYear=new Date().getFullYear();calMonth=new Date().getMonth();renderCalendar();}

// ── Search ─────────────────────────────────────────────────────────────────
function onSearch(val) {
  clearTimeout(searchTimer);
  if(!val.trim()){closeSearch();return;}
  searchTimer=setTimeout(()=>runSearch(val.trim()),250);
}

function onSearchFocus() {
  const val=document.getElementById('search-input').value.trim();
  if(val) runSearch(val);
}

async function runSearch(q) {
  const res=await fetch(`/api/search?q=${encodeURIComponent(q)}`).then(r=>r.json());
  const el=document.getElementById('search-results');
  const {notes:ns,reminders:rs}=res;
  if(!ns.length&&!rs.length){
    el.innerHTML=`<div class="sr-empty">Nothing found for "${esc(q)}"</div>`;
    el.classList.add('open'); return;
  }
  let html='';
  if(ns.length){
    html+=`<div class="sr-section">Notes</div>`;
    html+=ns.slice(0,6).map(n=>`
      <div class="sr-row" onclick="jumpToNote(${n.id})">
        <span class="sr-cat">${CAT_EMOJI[n.category]} ${n.category}</span>
        <span>${esc(n.content.substring(0,60))}${n.content.length>60?'…':''}</span>
      </div>`).join('');
  }
  if(rs.length){
    html+=`<div class="sr-section">Reminders</div>`;
    html+=rs.slice(0,4).map(r=>`
      <div class="sr-row" onclick="showView('calendar')">
        <span class="sr-cat">📅</span>
        <span>${esc(r.content.substring(0,50))} — ${fmtDate(r.remind_at)}</span>
      </div>`).join('');
  }
  el.innerHTML=html;
  el.classList.add('open');
}

function closeSearch() {
  document.getElementById('search-results').classList.remove('open');
}

function jumpToNote(id) {
  closeSearch();
  document.getElementById('search-input').value='';
  const note=notes.find(n=>n.id===id);
  if(note) showView(note.category==='general'?'all':note.category);
  // Brief highlight — find the row after render
  setTimeout(()=>{
    const rows=document.querySelectorAll('.note-row');
    rows.forEach(r=>{
      const ta=r.querySelector('.note-text');
      if(ta&&ta.textContent.trim()===note?.content){
        r.style.background='var(--amber-bg)';
        r.scrollIntoView({behavior:'smooth',block:'center'});
        setTimeout(()=>r.style.background='',1200);
      }
    });
  },300);
}

document.addEventListener('click',e=>{
  const wrap=document.querySelector('.search-wrap');
  if(!wrap.contains(e.target)) closeSearch();
});

// ── Add shortcut ───────────────────────────────────────────────────────────
function openAdd() {
  if(currentView==='calendar') document.getElementById('quick-reminder').focus();
  else document.getElementById('quick-note').focus();
}

// ── Counts ─────────────────────────────────────────────────────────────────
async function updateCounts() {
  const all=await fetch('/api/notes').then(r=>r.json());
  document.getElementById('cnt-all').textContent=all.length||'';
  CATS.forEach(cat=>{
    const n=all.filter(x=>x.category===cat).length;
    document.getElementById('cnt-'+cat).textContent=n||'';
  });
}

// ── Utils ──────────────────────────────────────────────────────────────────
function closeModal(id){document.getElementById(id).classList.remove('open');}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function fmtDate(s){
  if(!s) return '';
  return new Date(s).toLocaleDateString('en-CA',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
}
document.querySelectorAll('.overlay').forEach(o=>o.addEventListener('click',e=>{if(e.target===o)o.classList.remove('open');}));

loadNotes(null);
</script>
</body>
</html>"""

# ── iPad Display View ─────────────────────────────────────────────────────────
# Server-rendered, no modern JS, ES5 only, auto-refreshes every 60s.
# Designed for iPad Mini 2 in landscape — iOS 12 Safari compatible.
import calendar as _calendar

def get_display_data():
    con = db()
    now = datetime.now()
    reminders = con.execute(
        "SELECT id, content, remind_at, fired_at FROM reminders "
        "WHERE archived_at IS NULL ORDER BY remind_at ASC"
    ).fetchall()
    upcoming = []
    overdue  = []
    for r in reminders:
        try:
            dt = datetime.strptime(r["remind_at"], "%Y-%m-%d %H:%M")
            if dt < now:
                overdue.append(dict(r))
            else:
                upcoming.append(dict(r))
        except:
            upcoming.append(dict(r))
    notes_raw = con.execute(
        "SELECT id, category, content, tags, created_at FROM notes "
        "WHERE archived_at IS NULL ORDER BY created_at DESC"
    ).fetchall()
    grouped = {}
    for n in notes_raw:
        cat = n["category"]
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(dict(n))
    cal_reminders = {}
    for r in reminders:
        try:
            day = r["remind_at"][:10]
            if day not in cal_reminders:
                cal_reminders[day] = []
            cal_reminders[day].append(r["content"])
        except:
            pass
    con.close()
    return {"upcoming": upcoming[:8], "overdue": overdue,
            "grouped": grouped, "cal_reminders": cal_reminders, "now": now}

def build_calendar_html(now, cal_reminders):
    month_name = now.strftime("%B %Y")
    first_weekday, num_days = _calendar.monthrange(now.year, now.month)
    first_weekday = (first_weekday + 1) % 7
    days_html = ""
    for d in ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]:
        days_html += '<div class="ch">' + d + '</div>'
    for _ in range(first_weekday):
        days_html += '<div class="cd cd-empty"></div>'
    for day in range(1, num_days + 1):
        ds = "%s-%02d-%02d" % (now.year, now.month, day)
        is_today = (day == now.day)
        cls = "cd cd-today" if is_today else "cd"
        pills = ""
        if ds in cal_reminders:
            for content in cal_reminders[ds][:2]:
                short = content[:18] + (u"\u2026" if len(content) > 18 else "")
                try:
                    is_past = datetime.strptime(ds, "%Y-%m-%d") < now.replace(hour=0, minute=0, second=0)
                except:
                    is_past = False
                pcls = " pill-overdue" if is_past else ""
                pills += '<span class="pill' + pcls + '">' + short + '</span>'
            extra = len(cal_reminders[ds]) - 2
            if extra > 0:
                pills += '<span class="pill pill-more">+' + str(extra) + '</span>'
        days_html += '<div class="' + cls + '"><span class="dn">' + str(day) + '</span>' + pills + '</div>'
    return month_name, days_html

CAT_EMOJI = {"grocery":"\U0001f6d2","baby":"\U0001f37c","work":"\U0001f4bc",
             "health":"\u2764\ufe0f","finance":"\U0001f4b0","general":"\U0001f4dd"}

DISPLAY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>Ted's Board</title>
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;1,400&family=JetBrains+Mono:wght@400;500&family=Karla:wght@400;600;700&display=swap" rel="stylesheet">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: #1c1c1a;
  color: #f0ede6;
  font-family: Karla, sans-serif;
  font-size: 16px;
  line-height: 1.5;
  height: 100vh;
  overflow: hidden;
}

/* ── Two-column layout ── */
.board {
  display: -webkit-box;
  display: -webkit-flex;
  display: flex;
  height: 100vh;
}

/* LEFT — calendar + reminders */
.left {
  width: 54%;
  height: 100vh;
  display: -webkit-box;
  display: -webkit-flex;
  display: flex;
  -webkit-box-orient: vertical;
  -webkit-flex-direction: column;
  flex-direction: column;
  border-right: 2px solid #2e2e2b;
  overflow: hidden;
  background: #1c1c1a;
}

/* RIGHT — notes with swipeable categories */
.right {
  width: 46%;
  height: 100vh;
  display: -webkit-box;
  display: -webkit-flex;
  display: flex;
  -webkit-box-orient: vertical;
  -webkit-flex-direction: column;
  flex-direction: column;
  background: #141412;
  overflow: hidden;
}

/* ── Header ── */
.header {
  padding: 14px 20px 12px;
  border-bottom: 1px solid #2e2e2b;
  display: -webkit-box;
  display: -webkit-flex;
  display: flex;
  -webkit-box-align: baseline;
  -webkit-align-items: baseline;
  align-items: baseline;
  gap: 12px;
  -webkit-flex-shrink: 0;
  flex-shrink: 0;
}
.header-title {
  font-family: Lora, serif;
  font-size: 1.15rem;
  font-weight: 600;
  color: #f0ede6;
  letter-spacing: -0.01em;
}
.header-ts {
  font-family: JetBrains Mono, monospace;
  font-size: 0.72rem;
  color: #6b6a66;
  margin-left: auto;
}

/* ── Calendar ── */
.cal-wrap {
  padding: 14px 18px 8px;
  -webkit-flex-shrink: 0;
  flex-shrink: 0;
}
.cal-month {
  font-family: Lora, serif;
  font-size: 1.05rem;
  font-weight: 600;
  color: #f0ede6;
  margin-bottom: 10px;
  letter-spacing: -0.01em;
}
.cal-grid {
  display: -webkit-box;
  display: -webkit-flex;
  display: flex;
  -webkit-flex-wrap: wrap;
  flex-wrap: wrap;
  border: 1px solid #2e2e2b;
  border-radius: 8px;
  overflow: hidden;
}
.ch {
  width: 14.28%;
  text-align: center;
  font-family: JetBrains Mono, monospace;
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #6b6a66;
  padding: 5px 0 4px;
  background: #232320;
  border-bottom: 1px solid #2e2e2b;
}
.cd {
  width: 14.28%;
  min-height: 52px;
  padding: 4px 4px 3px;
  border-right: 1px solid #2e2e2b;
  border-bottom: 1px solid #2e2e2b;
  background: #1c1c1a;
  vertical-align: top;
}
.cd:nth-child(7n) { border-right: none; }
.cd-empty {
  background: #171715;
}
.cd-today {
  background: #2a2415 !important;
  border: 2px solid #d97706 !important;
  border-radius: 2px;
}
.dn {
  display: block;
  font-family: JetBrains Mono, monospace;
  font-size: 0.68rem;
  color: #5a5955;
  margin-bottom: 2px;
  font-weight: 400;
}
.cd-today .dn {
  color: #f59e0b;
  font-weight: 500;
  font-size: 0.78rem;
}
.pill {
  display: block;
  font-size: 0.56rem;
  background: #3a3a37;
  color: #c4c3be;
  border-radius: 3px;
  padding: 1px 4px;
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pill-overdue {
  background: #7c3c00;
  color: #fbbf24;
}
.pill-more {
  background: transparent;
  color: #5a5955;
}

/* ── Reminders section ── */
.rem-wrap {
  -webkit-flex: 1;
  flex: 1;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  padding: 0 18px 12px;
}
.sec-label {
  font-family: JetBrains Mono, monospace;
  font-size: 0.62rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  padding: 12px 0 6px;
  border-bottom: 1px solid #2e2e2b;
  margin-bottom: 4px;
}
.sec-label-overdue { color: #d97706; }
.sec-label-upcoming { color: #6b6a66; }

.rem-row {
  display: -webkit-box;
  display: -webkit-flex;
  display: flex;
  -webkit-box-align: start;
  -webkit-align-items: flex-start;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid #242420;
}
.rdot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-top: 6px;
  -webkit-flex-shrink: 0;
  flex-shrink: 0;
  background: #4a4a47;
}
.rdot-ov { background: #d97706; }
.rem-body { -webkit-flex: 1; flex: 1; }
.rem-text {
  font-size: 0.92rem;
  color: #e8e6df;
  font-weight: 600;
  line-height: 1.35;
}
.rem-text-ov { color: #fbbf24; }
.rem-time {
  font-family: JetBrains Mono, monospace;
  font-size: 0.62rem;
  color: #5a5955;
  margin-top: 2px;
}
.rem-time-ov { color: #d97706; }
.rem-fired {
  font-family: JetBrains Mono, monospace;
  font-size: 0.55rem;
  color: #3a3a37;
}
.empty-state {
  font-family: JetBrains Mono, monospace;
  font-size: 0.7rem;
  color: #3a3a37;
  padding: 16px 0;
  text-align: center;
}

/* ── Refresh bar ── */
.refresh-bar {
  background: #141412;
  border-top: 1px solid #2e2e2b;
  font-family: JetBrains Mono, monospace;
  font-size: 0.58rem;
  color: #3a3a37;
  text-align: center;
  padding: 5px;
  -webkit-flex-shrink: 0;
  flex-shrink: 0;
}

/* ── Notes right column ── */
.notes-header {
  padding: 14px 18px 10px;
  border-bottom: 2px solid #2e2e2b;
  -webkit-flex-shrink: 0;
  flex-shrink: 0;
}
.notes-title {
  font-family: Lora, serif;
  font-size: 1.05rem;
  font-weight: 600;
  color: #f0ede6;
  margin-bottom: 10px;
}

/* Category tabs — ES5 compatible tab strip */
.tab-strip {
  display: -webkit-box;
  display: -webkit-flex;
  display: flex;
  gap: 6px;
  -webkit-flex-wrap: wrap;
  flex-wrap: wrap;
}
.tab {
  font-family: JetBrains Mono, monospace;
  font-size: 0.62rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 4px 10px;
  border-radius: 4px;
  background: #2a2a27;
  color: #6b6a66;
  border: 1px solid #3a3a37;
  cursor: pointer;
  text-decoration: none;
  display: inline-block;
}
.tab-active {
  background: #f0ede6;
  color: #1c1c1a;
  border-color: #f0ede6;
}

/* Notes scroll area */
.notes-scroll {
  -webkit-flex: 1;
  flex: 1;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

/* Each category panel — shown/hidden via anchor + :target */
.cat-panel {
  display: none;
  padding: 14px 18px;
}
.cat-panel:first-child { display: block; }
.cat-panel:target { display: block; }

/* When any panel is targeted, hide the first one */
.notes-scroll:has(.cat-panel:target) .cat-panel:first-child {
  display: none;
}

.cat-panel-title {
  font-family: JetBrains Mono, monospace;
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: #6b6a66;
  margin-bottom: 10px;
  display: -webkit-box;
  display: -webkit-flex;
  display: flex;
  -webkit-box-align: center;
  -webkit-align-items: center;
  align-items: center;
  gap: 6px;
}
.cat-badge {
  background: #2a2a27;
  border-radius: 8px;
  padding: 1px 6px;
  font-size: 0.58rem;
  color: #5a5955;
}
.note-row {
  padding: 8px 0;
  border-bottom: 1px solid #242420;
  display: -webkit-box;
  display: -webkit-flex;
  display: flex;
  gap: 8px;
  -webkit-box-align: start;
  -webkit-align-items: flex-start;
  align-items: flex-start;
}
.note-row:last-child { border-bottom: none; }
.note-dash {
  color: #3a3a37;
  font-size: 0.8rem;
  margin-top: 2px;
  -webkit-flex-shrink: 0;
  flex-shrink: 0;
}
.note-body { -webkit-flex: 1; flex: 1; min-width: 0; }
.note-text {
  font-size: 0.88rem;
  color: #d4d2cb;
  line-height: 1.4;
  font-weight: 400;
}
.note-meta {
  display: -webkit-box;
  display: -webkit-flex;
  display: flex;
  -webkit-box-align: center;
  -webkit-align-items: center;
  align-items: center;
  gap: 5px;
  margin-top: 3px;
  -webkit-flex-wrap: wrap;
  flex-wrap: wrap;
}
.ntag {
  font-family: JetBrains Mono, monospace;
  font-size: 0.55rem;
  color: #6b6a66;
  background: #2a2a27;
  border-radius: 8px;
  padding: 1px 6px;
  border: 1px solid #3a3a37;
}
.ndate {
  font-family: JetBrains Mono, monospace;
  font-size: 0.55rem;
  color: #3a3a37;
}
.more-notes {
  font-family: JetBrains Mono, monospace;
  font-size: 0.62rem;
  color: #3a3a37;
  padding: 8px 0 4px;
  text-align: center;
}
.empty-notes {
  font-family: JetBrains Mono, monospace;
  font-size: 0.68rem;
  color: #3a3a37;
  padding: 20px 0;
  text-align: center;
}
</style>
</head>
<body>
<div class="board">

  <!-- ── LEFT COLUMN ── -->
  <div class="left">
    <div class="header">
      <span class="header-title">Ted's Board</span>
      <span class="header-ts">{{ now_str }}</span>
    </div>

    <div class="cal-wrap">
      <div class="cal-month">{{ month_name }}</div>
      <div class="cal-grid">{{ cal_html | safe }}</div>
    </div>

    <div class="rem-wrap">
      {% if overdue %}
      <div class="sec-label sec-label-overdue">&#9711; Overdue — {{ overdue|length }}</div>
      {% for r in overdue %}
      <div class="rem-row">
        <div class="rdot rdot-ov"></div>
        <div class="rem-body">
          <div class="rem-text rem-text-ov">{{ r.content }}</div>
          <div class="rem-time rem-time-ov">{{ r.remind_at }}</div>
        </div>
      </div>
      {% endfor %}
      {% endif %}

      {% if upcoming %}
      <div class="sec-label sec-label-upcoming">Upcoming — {{ upcoming|length }}</div>
      {% for r in upcoming %}
      <div class="rem-row">
        <div class="rdot"></div>
        <div class="rem-body">
          <div class="rem-text">{{ r.content }}</div>
          <div class="rem-time">
            {{ r.remind_at }}
            {% if r.fired_at %}<span class="rem-fired"> &middot; notified</span>{% endif %}
          </div>
        </div>
      </div>
      {% endfor %}
      {% else %}
      {% if not overdue %}
      <div class="empty-state">no reminders</div>
      {% endif %}
      {% endif %}
    </div>

    <div class="refresh-bar">auto-refresh 60s</div>
  </div>

  <!-- ── RIGHT COLUMN ── -->
  <div class="right">
    <div class="notes-header">
      <div class="notes-title">Notes</div>
      <div class="tab-strip">
        {% set ns = namespace(first=true) %}
        {% for cat in grouped.keys() %}
        <a href="#cat-{{ cat }}" class="tab {% if ns.first %}tab-active{% endif %}"
           onclick="switchTab(this)">
          {{ emoji.get(cat, '') }} {{ cat }}
        </a>
        {% set ns.first = false %}
        {% endfor %}
      </div>
    </div>

    <div class="notes-scroll" id="notes-scroll">
      {% set ns2 = namespace(first=true) %}
      {% for cat, notes in grouped.items() %}
      <div class="cat-panel" id="cat-{{ cat }}">
        <div class="cat-panel-title">
          {{ emoji.get(cat, '') }} {{ cat }}
          <span class="cat-badge">{{ notes|length }}</span>
        </div>
        {% for note in notes[:12] %}
        <div class="note-row">
          <span class="note-dash">&#8211;</span>
          <div class="note-body">
            <div class="note-text">{{ note.content }}</div>
            <div class="note-meta">
              {% if note.tags %}
              {% for tag in note.tags.split(',') %}
              {% if tag.strip() %}<span class="ntag">{{ tag.strip() }}</span>{% endif %}
              {% endfor %}
              {% endif %}
              <span class="ndate">{{ note.created_at[:10] }}</span>
            </div>
          </div>
        </div>
        {% endfor %}
        {% if notes|length > 12 %}
        <div class="more-notes">+ {{ notes|length - 12 }} more on main app</div>
        {% endif %}
        {% if not notes %}
        <div class="empty-notes">nothing here yet</div>
        {% endif %}
      </div>
      {% endfor %}
      {% if not grouped %}
      <div style="padding:24px 18px">
        <div class="empty-notes">no notes yet</div>
      </div>
      {% endif %}
    </div>
  </div>

</div>

<script>
// ES5 only — tab switching without any modern JS
function switchTab(el) {
  var strip = el.parentNode;
  var tabs = strip.getElementsByTagName('a');
  var i;
  for (i = 0; i < tabs.length; i++) {
    tabs[i].className = tabs[i].className.replace(' tab-active', '').replace('tab-active', '');
  }
  el.className = el.className + ' tab-active';

  // Show target panel, hide others
  var scroll = document.getElementById('notes-scroll');
  var panels = scroll.getElementsByTagName('div');
  var target = el.getAttribute('href').replace('#', '');
  for (i = 0; i < panels.length; i++) {
    if (panels[i].className.indexOf('cat-panel') !== -1) {
      if (panels[i].id === target) {
        panels[i].style.display = 'block';
      } else {
        panels[i].style.display = 'none';
      }
    }
  }
  return false;
}

// Show first panel on load
(function() {
  var scroll = document.getElementById('notes-scroll');
  var panels = scroll.getElementsByTagName('div');
  var shown = false;
  var i;
  for (i = 0; i < panels.length; i++) {
    if (panels[i].className.indexOf('cat-panel') !== -1) {
      if (!shown) {
        panels[i].style.display = 'block';
        shown = true;
      } else {
        panels[i].style.display = 'none';
      }
    }
  }
}());
</script>

</body>
</html>"""

@app.route("/display")
def display():
    data = get_display_data()
    month_name, cal_html = build_calendar_html(data["now"], data["cal_reminders"])
    return render_template_string(
        DISPLAY_HTML,
        now_str=data["now"].strftime("%a %b %d, %H:%M"),
        month_name=month_name,
        cal_html=cal_html,
        overdue=data["overdue"],
        upcoming=data["upcoming"],
        grouped=data["grouped"],
        emoji=CAT_EMOJI,
    )

@app.route("/")
def index():
    return render_template_string(HTML)

if __name__ == "__main__":
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    print("TedBot UI → http://0.0.0.0:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)