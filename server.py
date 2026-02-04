# ============================================
# TAP ROYALE - PYTHON SERVER
# Flask + SQLite
# ============================================

from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)  # Разрешаем запросы с любых доменов

# ============ DATABASE ============
DB_PATH = 'taproyale.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id TEXT UNIQUE NOT NULL,
            tg_name TEXT DEFAULT 'Player',
            nickname TEXT DEFAULT 'Player',
            gold INTEGER DEFAULT 0,
            gems INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            total_taps INTEGER DEFAULT 0,
            upgrade_tap INTEGER DEFAULT 0,
            upgrade_auto INTEGER DEFAULT 0,
            upgrade_exp INTEGER DEFAULT 0,
            referrer_id TEXT,
            referral_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_level ON users(level DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_gold ON users(gold DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_refs ON users(referral_count DESC)')
    conn.commit()
    conn.close()
    print("✅ Database initialized")

# ============ HELPERS ============
REFERRAL_BONUS = {
    'gold': 500,
    'gems': 3,
    'passive': 5
}

ARENAS = [
    {'minLevel': 1, 'name': '🏰 Тренировочный лагерь'},
    {'minLevel': 5, 'name': '⚔️ Деревня гоблинов'},
    {'minLevel': 12, 'name': '🏯 Костяной лес'},
    {'minLevel': 20, 'name': '🎪 Варварская арена'},
    {'minLevel': 30, 'name': '⛏️ Шахта сокровищ'},
    {'minLevel': 42, 'name': '🏛️ Королевский двор'},
    {'minLevel': 55, 'name': '🌋 Огненный пик'},
    {'minLevel': 70, 'name': '❄️ Ледяная пустошь'},
    {'minLevel': 88, 'name': '⚡ Небесная башня'},
    {'minLevel': 100, 'name': '👑 Легендарная арена'}
]

def get_arena_by_level(level):
    arena = ARENAS[0]
    for a in ARENAS:
        if level >= a['minLevel']:
            arena = a
    return arena['name']

def dict_from_row(row):
    return dict(row) if row else None

# ============ API ENDPOINTS ============

@app.route('/')
def home():
    return jsonify({
        'name': 'Tap Royale API',
        'version': '1.0',
        'endpoints': [
            'POST /api/sync',
            'POST /api/referral', 
            'GET /api/leaderboard?type=level|gold|refs',
            'GET /api/player/<tg_id>',
            'GET /api/stats'
        ]
    })

@app.route('/api/sync', methods=['POST'])
def sync():
    """Синхронизация данных игрока"""
    try:
        data = request.json
        tg_id = str(data.get('tg_id'))

        if not tg_id:
            return jsonify({'error': 'tg_id required'}), 400

        conn = get_db()
        cursor = conn.cursor()

        # Проверяем существует ли пользователь
        cursor.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
        user = cursor.fetchone()

        if user:
            # Обновляем (берём максимум чтобы не терять прогресс)
            cursor.execute('''
                UPDATE users SET
                    tg_name = COALESCE(?, tg_name),
                    nickname = COALESCE(?, nickname),
                    gold = MAX(gold, ?),
                    gems = MAX(gems, ?),
                    level = MAX(level, ?),
                    exp = ?,
                    total_taps = MAX(total_taps, ?),
                    upgrade_tap = MAX(upgrade_tap, ?),
                    upgrade_auto = MAX(upgrade_auto, ?),
                    upgrade_exp = MAX(upgrade_exp, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE tg_id = ?
            ''', (
                data.get('tg_name'),
                data.get('nickname'),
                data.get('gold', 0),
                data.get('gems', 0),
                data.get('level', 1),
                data.get('exp', 0),
                data.get('totalTaps', 0),
                data.get('upgrades', {}).get('tap', {}).get('level', 0),
                data.get('upgrades', {}).get('auto', {}).get('level', 0),
                data.get('upgrades', {}).get('exp', {}).get('level', 0),
                tg_id
            ))
        else:
            # Создаём нового
            cursor.execute('''
                INSERT INTO users (tg_id, tg_name, nickname, gold, gems, level, exp, total_taps, upgrade_tap, upgrade_auto, upgrade_exp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                tg_id,
                data.get('tg_name', 'Player'),
                data.get('nickname', data.get('tg_name', 'Player')),
                data.get('gold', 0),
                data.get('gems', 0),
                data.get('level', 1),
                data.get('exp', 0),
                data.get('totalTaps', 0),
                data.get('upgrades', {}).get('tap', {}).get('level', 0),
                data.get('upgrades', {}).get('auto', {}).get('level', 0),
                data.get('upgrades', {}).get('exp', {}).get('level', 0)
            ))

        conn.commit()

        # Получаем обновлённые данные
        cursor.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
        user = dict_from_row(cursor.fetchone())
        conn.close()

        return jsonify({
            'success': True,
            'referrals': user['referral_count'],
            'gold': user['gold'],
            'gems': user['gems'],
            'level': user['level']
        })

    except Exception as e:
        print(f"Sync error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/referral', methods=['POST'])
def referral():
    """Обработка реферала"""
    try:
        data = request.json
        new_user_id = str(data.get('new_user_id'))
        referrer_id = str(data.get('referrer_id'))

        if not new_user_id or not referrer_id:
            return jsonify({'error': 'Missing parameters'}), 400

        if new_user_id == referrer_id:
            return jsonify({'success': False, 'reason': 'self_referral'})

        conn = get_db()
        cursor = conn.cursor()

        # Проверяем нового пользователя
        cursor.execute('SELECT * FROM users WHERE tg_id = ?', (new_user_id,))
        new_user = cursor.fetchone()

        if new_user and new_user['referrer_id']:
            conn.close()
            return jsonify({'success': False, 'reason': 'already_referred'})

        # Проверяем/создаём пригласившего
        cursor.execute('SELECT * FROM users WHERE tg_id = ?', (referrer_id,))
        referrer = cursor.fetchone()

        if not referrer:
            cursor.execute('INSERT INTO users (tg_id) VALUES (?)', (referrer_id,))

        # Создаём/обновляем нового пользователя
        if not new_user:
            cursor.execute('''
                INSERT INTO users (tg_id, gold, gems, referrer_id)
                VALUES (?, ?, ?, ?)
            ''', (new_user_id, REFERRAL_BONUS['gold'], REFERRAL_BONUS['gems'], referrer_id))
        else:
            cursor.execute('''
                UPDATE users SET
                    referrer_id = ?,
                    gold = gold + ?,
                    gems = gems + ?
                WHERE tg_id = ?
            ''', (referrer_id, REFERRAL_BONUS['gold'], REFERRAL_BONUS['gems'], new_user_id))

        # Бонус пригласившему
        cursor.execute('''
            UPDATE users SET
                referral_count = referral_count + 1,
                gold = gold + ?,
                gems = gems + ?
            WHERE tg_id = ?
        ''', (REFERRAL_BONUS['gold'], REFERRAL_BONUS['gems'], referrer_id))

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'bonus': {
                'gold': REFERRAL_BONUS['gold'],
                'gems': REFERRAL_BONUS['gems']
            }
        })

    except Exception as e:
        print(f"Referral error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/leaderboard', methods=['GET'])
def leaderboard():
    """Получение рейтинга"""
    try:
        lb_type = request.args.get('type', 'level')

        order_by = 'level DESC, gold DESC'
        if lb_type == 'gold':
            order_by = 'gold DESC, level DESC'
        elif lb_type == 'refs':
            order_by = 'referral_count DESC, level DESC'

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(f'''
            SELECT tg_id, nickname, level, gold, referral_count as referrals
            FROM users
            ORDER BY {order_by}
            LIMIT 50
        ''')

        players = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Добавляем арену
        for p in players:
            p['arena'] = get_arena_by_level(p['level'])

        return jsonify(players)

    except Exception as e:
        print(f"Leaderboard error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/player/<tg_id>', methods=['GET'])
def get_player(tg_id):
    """Получение профиля игрока"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
        user = cursor.fetchone()

        if not user:
            conn.close()
            return jsonify({'error': 'Player not found'}), 404

        user = dict(user)
        user['arena'] = get_arena_by_level(user['level'])

        # Позиция в рейтинге
        cursor.execute('''
            SELECT COUNT(*) + 1 as rank FROM users 
            WHERE level > ? OR (level = ? AND gold > ?)
        ''', (user['level'], user['level'], user['gold']))
        user['rank_level'] = cursor.fetchone()['rank']

        cursor.execute('SELECT COUNT(*) + 1 as rank FROM users WHERE gold > ?', (user['gold'],))
        user['rank_gold'] = cursor.fetchone()['rank']

        conn.close()
        return jsonify(user)

    except Exception as e:
        print(f"Player error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def stats():
    """Статистика сервера"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) as count FROM users')
        total_players = cursor.fetchone()['count']

        cursor.execute('SELECT COALESCE(SUM(total_taps), 0) as sum FROM users')
        total_taps = cursor.fetchone()['sum']

        cursor.execute('SELECT COALESCE(SUM(gold), 0) as sum FROM users')
        total_gold = cursor.fetchone()['sum']

        conn.close()

        return jsonify({
            'totalPlayers': total_players,
            'totalTaps': total_taps,
            'totalGold': total_gold
        })

    except Exception as e:
        print(f"Stats error: {e}")
        return jsonify({'error': str(e)}), 500

# ============ START ============
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    print(f"🎮 Tap Royale Server starting on port {port}")
    print("📊 API endpoints:")
    print("   POST /api/sync - синхронизация данных")
    print("   POST /api/referral - обработка рефералов")
    print("   GET /api/leaderboard?type=level|gold|refs - рейтинг")
    print("   GET /api/player/<tg_id> - профиль игрока")
    print("   GET /api/stats - статистика")
    app.run(host='0.0.0.0', port=port, debug=False)
