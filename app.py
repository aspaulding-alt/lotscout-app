from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import json
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'lotscout-secret-key-change-in-production')

# ─── DATABASE ──────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lotscout.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            subscription_tier TEXT DEFAULT 'starter',
            keywords TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            trial_started TEXT,
            trial_active INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ─── BROAD KEYWORD BLOCK LIST ─────────────────────────────────────────────────
BLOCKED_KEYWORDS = [
    "baseball cards", "football cards", "basketball cards", "hockey cards",
    "pokemon", "pokemon cards", "trading cards", "sports cards",
    "coins", "stamps", "jewelry", "furniture", "antiques",
    "vintage", "collectibles", "toys", "books", "records", "vinyl"
]

# ─── TIER LIMITS ──────────────────────────────────────────────────────────────
TIER_LIMITS = {
    'starter':  5,
    'standard': 20,
    'pro':      999999  # unlimited
}

# ─── AUTH DECORATOR ───────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def pro_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        conn.close()
        if not user or user['subscription_tier'] != 'pro':
            return redirect(url_for('dashboard', upgrade=True))
        return f(*args, **kwargs)
    return decorated

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()
        tier     = request.form.get('tier', 'starter')

        if not email or not password:
            return render_template('signup.html', error='Email and password are required.')

        if len(password) < 8:
            return render_template('signup.html', error='Password must be at least 8 characters.')

        try:
            conn = get_db()
            conn.execute(
                'INSERT INTO users (email, password, subscription_tier, trial_started, trial_active) VALUES (?, ?, ?, ?, ?)',
                (email, generate_password_hash(password), tier, datetime.now().isoformat(), 1 if tier == 'pro' else 0)
            )
            conn.commit()

            # Log them in immediately
            user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
            conn.close()

            session['user_id'] = user['id']
            session['email']   = user['email']
            session['tier']    = user['subscription_tier']

            return redirect(url_for('dashboard'))

        except sqlite3.IntegrityError:
            return render_template('signup.html', error='An account with that email already exists.')

    tier = request.args.get('tier', 'starter')
    return render_template('signup.html', tier=tier)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()

        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()

        if not user or not check_password_hash(user['password'], password):
            return render_template('login.html', error='Invalid email or password.')

        session['user_id'] = user['id']
        session['email']   = user['email']
        session['tier']    = user['subscription_tier']

        return redirect(url_for('dashboard'))

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()

    keywords   = json.loads(user['keywords']) if user['keywords'] else []
    tier       = user['subscription_tier']
    kw_limit   = TIER_LIMITS.get(tier, 5)
    upgrade    = request.args.get('upgrade', False)

    return render_template('dashboard.html',
        user=user,
        keywords=keywords,
        tier=tier,
        kw_limit=kw_limit,
        kw_count=len(keywords),
        upgrade=upgrade
    )

@app.route('/keywords/add', methods=['POST'])
@login_required
def add_keyword():
    data    = request.get_json()
    keyword = data.get('keyword', '').strip().lower()

    if not keyword:
        return jsonify({'error': 'Keyword cannot be empty'}), 400

    # Check broad keyword block list
    if keyword in BLOCKED_KEYWORDS:
        return jsonify({
            'error': f'"{keyword}" is too broad. Please be more specific — try something like "1952 Topps Mickey Mantle" instead of "baseball cards".'
        }), 400

    conn = get_db()
    user     = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    keywords = json.loads(user['keywords']) if user['keywords'] else []
    tier     = user['subscription_tier']
    kw_limit = TIER_LIMITS.get(tier, 5)

    if len(keywords) >= kw_limit:
        conn.close()
        return jsonify({'error': f'You\'ve reached your {kw_limit} keyword limit. Upgrade to add more.'}), 400

    if keyword in keywords:
        conn.close()
        return jsonify({'error': 'That keyword is already in your list.'}), 400

    keywords.append(keyword)
    conn.execute('UPDATE users SET keywords = ? WHERE id = ?', (json.dumps(keywords), session['user_id']))
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'keywords': keywords})

@app.route('/keywords/delete', methods=['POST'])
@login_required
def delete_keyword():
    data    = request.get_json()
    keyword = data.get('keyword', '').strip().lower()

    conn     = get_db()
    user     = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    keywords = json.loads(user['keywords']) if user['keywords'] else []

    if keyword in keywords:
        keywords.remove(keyword)
        conn.execute('UPDATE users SET keywords = ? WHERE id = ?', (json.dumps(keywords), session['user_id']))
        conn.commit()

    conn.close()
    return jsonify({'success': True, 'keywords': keywords})

@app.route('/hunter')
@pro_required
def hunter():
    return render_template('hunter.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

# ─── RUN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
