from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import pyotp, os, threading
from functools import wraps
from db import init_db, db, get_setting, set_setting, get_all_settings, save_parsed_articles
from parser_engine import parse_source

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-in-production')

ADMIN_USER     = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
TOTP_SECRET    = os.environ.get('TOTP_SECRET', 'JBSWY3DPEHPK3PXP')

try:
    init_db()
except Exception as e:
    print(f"[WARN] DB init failed: {e}")

# In-memory test cache (per-session, resets on restart — intentional)
_test_cache = {}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ===================== AUTH =====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'check_credentials':
            if request.form.get('username','').strip() == ADMIN_USER and \
               request.form.get('password','').strip() == ADMIN_PASSWORD:
                session['login_step'] = 2
                return redirect(url_for('login'))
            error = 'Неверный логин или пароль'
            session['login_step'] = 1
        elif action == 'check_totp':
            if pyotp.TOTP(TOTP_SECRET).verify(request.form.get('totp_code','').strip()):
                session['logged_in'] = True
                session['login_step'] = 1
                return redirect(url_for('parser'))
            error = 'Неверный код. Попробуйте ещё раз.'
            session['login_step'] = 2
    return render_template('login.html', step=session.get('login_step',1), error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ===================== PAGES =====================

@app.route('/')
@login_required
def index():
    return redirect(url_for('parser'))

@app.route('/parser')
@login_required
def parser():
    cf   = request.args.get('country','')
    sf   = request.args.get('source','')
    catf = request.args.get('category','')
    stf  = request.args.get('status','')
    try:
        with db() as conn:
            cur = conn.cursor()
            q = """SELECT n.*, s.name as source_name, c.name as country_name
                   FROM news n
                   LEFT JOIN sources s ON n.source_id = s.id
                   LEFT JOIN countries c ON s.country_id = c.id
                   WHERE 1=1"""
            p = []
            if cf:   q += " AND c.name=%s";     p.append(cf)
            if sf:   q += " AND s.name=%s";     p.append(sf)
            if catf: q += " AND s.category=%s"; p.append(catf)
            if stf:  q += " AND n.status=%s";   p.append(stf)
            q += " ORDER BY n.parsed_at DESC LIMIT 100"
            cur.execute(q, p)
            news = cur.fetchall()
            cur.execute("SELECT DISTINCT c.name FROM countries c JOIN sources s ON s.country_id=c.id")
            countries = [r['name'] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT name FROM sources")
            sources = [r['name'] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT category FROM sources")
            categories = [r['category'] for r in cur.fetchall()]
    except Exception as e:
        print(f"[DB] {e}")
        news=countries=sources=categories=[]
    return render_template('parser.html', news=news, countries=countries,
                           sources=sources, categories=categories,
                           filters=dict(country=cf,source=sf,category=catf,status=stf))

@app.route('/map')
@login_required
def map_view():
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT map_code, name FROM countries WHERE active=TRUE AND map_code!=''")
            active = [dict(r) for r in cur.fetchall()]
    except Exception:
        active = []
    return render_template('map.html', active_countries=active)

@app.route('/settings')
@login_required
def settings():
    active_tab = request.args.get('tab','countries')
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM countries ORDER BY name")
            countries = cur.fetchall()
            cur.execute("""SELECT s.*, c.name as country_name
                           FROM sources s LEFT JOIN countries c ON s.country_id=c.id
                           ORDER BY s.name""")
            sources = cur.fetchall()
            cur.execute("""SELECT t.*, c.name as country_name
                           FROM telegram_channels t LEFT JOIN countries c ON t.country_id=c.id
                           ORDER BY t.name""")
            telegram = cur.fetchall()
            cur.execute("SELECT id, name FROM countries ORDER BY name")
            countries_list = cur.fetchall()
    except Exception as e:
        print(f"[DB] {e}")
        countries=sources=telegram=countries_list=[]
    cfg = get_all_settings()
    return render_template('settings.html', countries=countries, sources=sources,
                           telegram=telegram, countries_list=countries_list, cfg=cfg, active_tab=active_tab)

@app.route('/test')
@login_required
def test_parser():
    return render_template('test_parser.html')

@app.route('/analytics')
@login_required
def analytics():
    return render_template('analytics.html')

# ===================== API — COUNTRIES =====================

@app.route('/api/countries', methods=['POST'])
@login_required
def add_country():
    d = request.json
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO countries (name,language,telegram_channel,map_code,active)
                VALUES (%s,%s,%s,%s,TRUE) RETURNING id""",
                (d['name'], d.get('language','en'), d.get('telegram_channel',''), d.get('map_code','')))
            return jsonify({'status':'ok','id':cur.fetchone()['id']})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 400

@app.route('/api/countries/<int:cid>', methods=['PUT'])
@login_required
def update_country(cid):
    d = request.json
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("""UPDATE countries SET name=%s,language=%s,telegram_channel=%s,map_code=%s
                WHERE id=%s""",
                (d['name'],d.get('language','en'),d.get('telegram_channel',''),d.get('map_code',''),cid))
            return jsonify({'status':'ok'})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 400

@app.route('/api/countries/<int:cid>', methods=['DELETE'])
@login_required
def delete_country(cid):
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM countries WHERE id=%s",(cid,))
            return jsonify({'status':'ok'})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 400

# ===================== API — SOURCES =====================

def _async_initial_parse(source_id, source_cfg):
    """Run initial parse of 10 articles in background thread."""
    try:
        dedup_url   = get_setting('dedup_by_url',   'true') == 'true'
        dedup_title = get_setting('dedup_by_title',  'true') == 'true'
        result = parse_source(source_cfg, max_items=10)
        if result.get('items'):
            saved = save_parsed_articles(source_id, result['items'], dedup_url, dedup_title)
            print(f"[INIT PARSE] source={source_id} saved={saved}")
    except Exception as e:
        print(f"[INIT PARSE ERROR] source={source_id}: {e}")

@app.route('/api/sources', methods=['POST'])
@login_required
def add_source():
    d = request.json
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO sources
                (name,url,rss_url,country_id,category,enabled,interval_min,max_news,
                 parser_type,css_item,css_title,css_link,base_url)
                VALUES (%s,%s,%s,%s,%s,TRUE,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (d['name'], d.get('url',''), d.get('rss_url',''),
                 d.get('country_id') or None, d.get('category','General'),
                 int(d.get('interval_min',30)), int(d.get('max_news',10)),
                 d.get('parser_type','rss'),
                 d.get('css_item',''), d.get('css_title',''),
                 d.get('css_link',''), d.get('base_url','')))
            new_id = cur.fetchone()['id']

        # Kick off initial parse of 10 latest articles in background
        source_cfg = {
            'parser_type': d.get('parser_type','rss'),
            'url':         d.get('url',''),
            'rss_url':     d.get('rss_url',''),
            'css_item':    d.get('css_item',''),
            'css_title':   d.get('css_title',''),
            'css_link':    d.get('css_link',''),
            'base_url':    d.get('base_url',''),
        }
        t = threading.Thread(target=_async_initial_parse, args=(new_id, source_cfg), daemon=True)
        t.start()

        return jsonify({'status':'ok','id':new_id})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 400

@app.route('/api/sources/<int:sid>', methods=['PUT'])
@login_required
def update_source(sid):
    d = request.json
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("""UPDATE sources SET
                name=%s,url=%s,rss_url=%s,country_id=%s,category=%s,
                interval_min=%s,max_news=%s,parser_type=%s,
                css_item=%s,css_title=%s,css_link=%s,base_url=%s
                WHERE id=%s""",
                (d['name'],d.get('url',''),d.get('rss_url',''),
                 d.get('country_id') or None, d.get('category','General'),
                 int(d.get('interval_min',30)),int(d.get('max_news',10)),
                 d.get('parser_type','rss'),
                 d.get('css_item',''),d.get('css_title',''),
                 d.get('css_link',''),d.get('base_url',''),sid))
            return jsonify({'status':'ok'})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 400

@app.route('/api/sources/<int:sid>/toggle', methods=['POST'])
@login_required
def toggle_source(sid):
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE sources SET enabled=NOT enabled WHERE id=%s RETURNING enabled",(sid,))
            return jsonify({'status':'ok','enabled':cur.fetchone()['enabled']})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 400

@app.route('/api/sources/<int:sid>', methods=['DELETE'])
@login_required
def delete_source(sid):
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM sources WHERE id=%s",(sid,))
            return jsonify({'status':'ok'})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 400

# ===================== API — INSTANT PARSE =====================

@app.route('/api/parse/instant', methods=['POST'])
@login_required
def instant_parse():
    """Немедленно парсит все включённые источники."""
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sources WHERE enabled=TRUE")
            sources_list = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 400

    dedup_url   = get_setting('dedup_by_url',   'true') == 'true'
    dedup_title = get_setting('dedup_by_title',  'true') == 'true'

    total_saved  = 0
    total_errors = 0
    for src in sources_list:
        try:
            result = parse_source(src, max_items=src.get('max_news', 10))
            if result.get('items'):
                saved = save_parsed_articles(src['id'], result['items'], dedup_url, dedup_title)
                total_saved += saved
        except Exception as e:
            print(f"[INSTANT PARSE] source={src['id']} error: {e}")
            total_errors += 1

    msg = f'Готово: сохранено {total_saved} новых материалов'
    if total_errors:
        msg += f', ошибок: {total_errors}'
    return jsonify({'status':'ok', 'saved': total_saved, 'errors': total_errors, 'message': msg})

# ===================== API — TEST PARSER =====================

@app.route('/api/test-parse', methods=['POST'])
@login_required
def test_parse():
    d = request.json
    source_cfg = {
        'parser_type': d.get('parser_type','rss'),
        'url':         d.get('url',''),
        'rss_url':     d.get('url',''),
        'css_item':    d.get('css_item',''),
        'css_title':   d.get('css_title',''),
        'css_link':    d.get('css_link',''),
        'base_url':    d.get('base_url',''),
    }
    result = parse_source(source_cfg, max_items=int(d.get('max_items',20)))
    sid = session.get('_id')
    if not sid:
        import uuid; sid = str(uuid.uuid4()); session['_id'] = sid
    _test_cache[sid] = result
    return jsonify(result)

# ===================== API — SETTINGS =====================

@app.route('/api/settings/chatgpt', methods=['POST'])
@login_required
def save_chatgpt():
    d = request.json
    set_setting('chatgpt_api_key',      d.get('api_key',''))
    set_setting('chatgpt_model',        d.get('model','gpt-4o'))
    set_setting('chatgpt_temperature',  d.get('temperature','0.7'))
    set_setting('chatgpt_max_tokens',   d.get('max_tokens','1000'))
    return jsonify({'status':'ok','message':'Настройки ChatGPT сохранены'})

@app.route('/api/settings/prompt', methods=['POST'])
@login_required
def save_prompt():
    set_setting('prompt', request.json.get('prompt',''))
    return jsonify({'status':'ok','message':'Промпт сохранён'})

@app.route('/api/settings/automation', methods=['POST'])
@login_required
def save_automation():
    d = request.json
    set_setting('auto_parse',   'true' if d.get('auto_parse')   else 'false')
    set_setting('auto_chatgpt', 'true' if d.get('auto_chatgpt') else 'false')
    set_setting('auto_draft',   'true' if d.get('auto_draft')   else 'false')
    return jsonify({'status':'ok','message':'Настройки автоматизации сохранены'})

@app.route('/api/settings/dedup', methods=['POST'])
@login_required
def save_dedup():
    d = request.json
    set_setting('dedup_by_url',   'true' if d.get('by_url')   else 'false')
    set_setting('dedup_by_title', 'true' if d.get('by_title') else 'false')
    return jsonify({'status':'ok','message':'Настройки антидубликатов сохранены'})

# ===================== API — TELEGRAM =====================

@app.route('/api/telegram', methods=['POST'])
@login_required
def add_telegram():
    d = request.json
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO telegram_channels (name,bot_token,chat_id,country_id,status)
                VALUES (%s,%s,%s,%s,'unknown') RETURNING id""",
                (d['name'],d['bot_token'],d['chat_id'],d.get('country_id') or None))
            return jsonify({'status':'ok','id':cur.fetchone()['id']})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 400

@app.route('/api/telegram/<int:tid>', methods=['DELETE'])
@login_required
def delete_telegram(tid):
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM telegram_channels WHERE id=%s",(tid,))
            return jsonify({'status':'ok'})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 400

# ===================== API — NEWS =====================

@app.route('/api/news/<int:nid>', methods=['DELETE'])
@login_required
def delete_news(nid):
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM news WHERE id=%s", (nid,))
            return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

@app.route('/api/news/<int:nid>/action', methods=['POST'])
@login_required
def news_action(nid):
    action = request.json.get('action')
    status_map = {'publish':'published','draft':'draft','reject':'rejected'}
    if action in status_map:
        try:
            with db() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE news SET status=%s WHERE id=%s",(status_map[action],nid))
            return jsonify({'status':'ok','new_status':status_map[action]})
        except Exception as e:
            return jsonify({'status':'error','message':str(e)}), 400
    return jsonify({'status':'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)
