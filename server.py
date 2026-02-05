# ============================================
# TAP ROYALE SERVER v2
# Гильдии + Казна
# ============================================

from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_PATH = 'taproyale.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()

    # Пользователи
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id TEXT UNIQUE NOT NULL,
            nickname TEXT DEFAULT 'Player',
            gold INTEGER DEFAULT 0,
            gems INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            total_taps INTEGER DEFAULT 0,
            referrer_id TEXT,
            referral_count INTEGER DEFAULT 0,
            guild_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Гильдии
    conn.execute('''
        CREATE TABLE IF NOT EXISTS guilds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            leader_id TEXT NOT NULL,
            treasury INTEGER DEFAULT 0,
            total_level INTEGER DEFAULT 0,
            member_count INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Участники гильдий
    conn.execute('''
        CREATE TABLE IF NOT EXISTS guild_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            tg_id TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            donated INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id, tg_id)
        )
    ''')

    conn.execute('CREATE INDEX IF NOT EXISTS idx_guild_members ON guild_members(guild_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_user_guild ON users(guild_id)')
    conn.commit()
    conn.close()
    print("✅ Database initialized with guilds")

# ============ USER API ============

@app.route('/')
def home():
    return jsonify({'name': 'Tap Royale API', 'version': '2.0'})

@app.route('/api/sync', methods=['POST'])
def sync():
    try:
        data = request.json
        tg_id = str(data.get('tg_id'))
        if not tg_id: return jsonify({'error': 'tg_id required'}), 400

        conn = get_db()
        cur = conn.cursor()

        cur.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
        user = cur.fetchone()

        if user:
            cur.execute('''
                UPDATE users SET
                    nickname = COALESCE(?, nickname),
                    gold = MAX(gold, ?),
                    gems = MAX(gems, ?),
                    level = MAX(level, ?),
                    total_taps = MAX(total_taps, ?)
                WHERE tg_id = ?
            ''', (
                data.get('nickname'),
                data.get('gold', 0),
                data.get('gems', 0),
                data.get('level', 1),
                data.get('totalTaps', 0),
                tg_id
            ))
        else:
            cur.execute('''
                INSERT INTO users (tg_id, nickname, gold, gems, level, total_taps)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (tg_id, data.get('nickname', 'Player'), data.get('gold', 0), data.get('gems', 0), data.get('level', 1), data.get('totalTaps', 0)))

        conn.commit()
        cur.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
        user = dict(cur.fetchone())
        conn.close()

        return jsonify({'success': True, 'referrals': user.get('referral_count', 0)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/referral', methods=['POST'])
def referral():
    try:
        data = request.json
        new_id = str(data.get('new_user_id'))
        ref_id = str(data.get('referrer_id'))

        if not new_id or not ref_id or new_id == ref_id:
            return jsonify({'success': False})

        conn = get_db()
        cur = conn.cursor()

        cur.execute('SELECT referrer_id FROM users WHERE tg_id = ?', (new_id,))
        user = cur.fetchone()
        if user and user['referrer_id']:
            conn.close()
            return jsonify({'success': False, 'reason': 'already_referred'})

        # Создаём/обновляем
        cur.execute('INSERT OR IGNORE INTO users (tg_id) VALUES (?)', (ref_id,))
        cur.execute('INSERT OR IGNORE INTO users (tg_id) VALUES (?)', (new_id,))

        cur.execute('UPDATE users SET referrer_id = ?, gold = gold + 500, gems = gems + 3 WHERE tg_id = ? AND referrer_id IS NULL', (ref_id, new_id))
        if cur.rowcount > 0:
            cur.execute('UPDATE users SET referral_count = referral_count + 1, gold = gold + 500, gems = gems + 3 WHERE tg_id = ?', (ref_id,))

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'bonus': {'gold': 500, 'gems': 3}})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/leaderboard', methods=['GET'])
def leaderboard():
    try:
        lb_type = request.args.get('type', 'level')
        order = 'level DESC, gold DESC'
        if lb_type == 'gold': order = 'gold DESC'
        elif lb_type == 'refs': order = 'referral_count DESC'

        conn = get_db()
        cur = conn.cursor()
        cur.execute(f'SELECT tg_id, nickname, level, gold, referral_count as referrals FROM users ORDER BY {order} LIMIT 50')
        players = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify(players)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ GUILD API ============

@app.route('/api/guilds', methods=['GET'])
def get_guilds():
    """Список всех гильдий"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''
            SELECT g.*, u.nickname as leader_name
            FROM guilds g
            LEFT JOIN users u ON g.leader_id = u.tg_id
            ORDER BY g.total_level DESC
            LIMIT 50
        ''')
        guilds = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify(guilds)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/guild/create', methods=['POST'])
def create_guild():
    """Создать гильдию (5 гемов)"""
    try:
        data = request.json
        tg_id = str(data.get('tg_id'))
        name = data.get('name', '').strip()

        if not tg_id or not name or len(name) < 2:
            return jsonify({'error': 'Invalid data'}), 400

        conn = get_db()
        cur = conn.cursor()

        # Проверяем гемы
        cur.execute('SELECT gems, guild_id, level FROM users WHERE tg_id = ?', (tg_id,))
        user = cur.fetchone()
        if not user or user['gems'] < 5:
            conn.close()
            return jsonify({'error': 'Not enough gems'}), 400
        if user['guild_id']:
            conn.close()
            return jsonify({'error': 'Already in guild'}), 400

        # Проверяем имя
        cur.execute('SELECT id FROM guilds WHERE name = ?', (name,))
        if cur.fetchone():
            conn.close()
            return jsonify({'error': 'Name taken'}), 400

        # Создаём гильдию
        cur.execute('UPDATE users SET gems = gems - 5 WHERE tg_id = ?', (tg_id,))
        cur.execute('INSERT INTO guilds (name, leader_id, total_level) VALUES (?, ?, ?)', (name, tg_id, user['level']))
        guild_id = cur.lastrowid

        cur.execute('UPDATE users SET guild_id = ? WHERE tg_id = ?', (guild_id, tg_id))
        cur.execute('INSERT INTO guild_members (guild_id, tg_id, role) VALUES (?, ?, ?)', (guild_id, tg_id, 'leader'))

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'guild_id': guild_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/guild/join', methods=['POST'])
def join_guild():
    """Вступить в гильдию"""
    try:
        data = request.json
        tg_id = str(data.get('tg_id'))
        guild_id = data.get('guild_id')

        conn = get_db()
        cur = conn.cursor()

        cur.execute('SELECT guild_id, level FROM users WHERE tg_id = ?', (tg_id,))
        user = cur.fetchone()
        if not user:
            conn.close()
            return jsonify({'error': 'User not found'}), 404
        if user['guild_id']:
            conn.close()
            return jsonify({'error': 'Already in guild'}), 400

        cur.execute('SELECT * FROM guilds WHERE id = ?', (guild_id,))
        guild = cur.fetchone()
        if not guild:
            conn.close()
            return jsonify({'error': 'Guild not found'}), 404
        if guild['member_count'] >= 20:
            conn.close()
            return jsonify({'error': 'Guild full'}), 400

        cur.execute('UPDATE users SET guild_id = ? WHERE tg_id = ?', (guild_id, tg_id))
        cur.execute('INSERT INTO guild_members (guild_id, tg_id, role) VALUES (?, ?, ?)', (guild_id, tg_id, 'member'))
        cur.execute('UPDATE guilds SET member_count = member_count + 1, total_level = total_level + ? WHERE id = ?', (user['level'], guild_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/guild/leave', methods=['POST'])
def leave_guild():
    """Покинуть гильдию"""
    try:
        data = request.json
        tg_id = str(data.get('tg_id'))

        conn = get_db()
        cur = conn.cursor()

        cur.execute('SELECT guild_id, level FROM users WHERE tg_id = ?', (tg_id,))
        user = cur.fetchone()
        if not user or not user['guild_id']:
            conn.close()
            return jsonify({'error': 'Not in guild'}), 400

        guild_id = user['guild_id']

        cur.execute('SELECT leader_id FROM guilds WHERE id = ?', (guild_id,))
        guild = cur.fetchone()

        # Если лидер - удаляем гильдию или передаём лидера
        if guild['leader_id'] == tg_id:
            cur.execute('SELECT tg_id FROM guild_members WHERE guild_id = ? AND tg_id != ? LIMIT 1', (guild_id, tg_id))
            new_leader = cur.fetchone()
            if new_leader:
                cur.execute('UPDATE guilds SET leader_id = ? WHERE id = ?', (new_leader['tg_id'], guild_id))
                cur.execute('UPDATE guild_members SET role = ? WHERE guild_id = ? AND tg_id = ?', ('leader', guild_id, new_leader['tg_id']))
            else:
                # Удаляем гильдию если никого не осталось
                cur.execute('DELETE FROM guilds WHERE id = ?', (guild_id,))

        cur.execute('DELETE FROM guild_members WHERE guild_id = ? AND tg_id = ?', (guild_id, tg_id))
        cur.execute('UPDATE users SET guild_id = NULL WHERE tg_id = ?', (tg_id,))
        cur.execute('UPDATE guilds SET member_count = member_count - 1, total_level = total_level - ? WHERE id = ?', (user['level'], guild_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/guild/<int:guild_id>', methods=['GET'])
def get_guild(guild_id):
    """Информация о гильдии"""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute('SELECT * FROM guilds WHERE id = ?', (guild_id,))
        guild = cur.fetchone()
        if not guild:
            conn.close()
            return jsonify({'error': 'Not found'}), 404

        guild = dict(guild)

        cur.execute('''
            SELECT gm.*, u.nickname, u.level, u.gold
            FROM guild_members gm
            JOIN users u ON gm.tg_id = u.tg_id
            WHERE gm.guild_id = ?
            ORDER BY gm.role DESC, gm.donated DESC
        ''', (guild_id,))
        guild['members'] = [dict(r) for r in cur.fetchall()]

        conn.close()
        return jsonify(guild)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/guild/my', methods=['GET'])
def get_my_guild():
    """Моя гильдия"""
    try:
        tg_id = request.args.get('tg_id')
        if not tg_id:
            return jsonify({'error': 'tg_id required'}), 400

        conn = get_db()
        cur = conn.cursor()

        cur.execute('SELECT guild_id FROM users WHERE tg_id = ?', (tg_id,))
        user = cur.fetchone()
        if not user or not user['guild_id']:
            conn.close()
            return jsonify({'guild': None})

        cur.execute('SELECT * FROM guilds WHERE id = ?', (user['guild_id'],))
        guild = cur.fetchone()
        if not guild:
            conn.close()
            return jsonify({'guild': None})

        guild = dict(guild)

        cur.execute('''
            SELECT gm.*, u.nickname, u.level
            FROM guild_members gm
            JOIN users u ON gm.tg_id = u.tg_id
            WHERE gm.guild_id = ?
            ORDER BY gm.role DESC, gm.donated DESC
        ''', (guild['id'],))
        guild['members'] = [dict(r) for r in cur.fetchall()]

        # Роль пользователя
        cur.execute('SELECT role FROM guild_members WHERE guild_id = ? AND tg_id = ?', (guild['id'], tg_id))
        role = cur.fetchone()
        guild['my_role'] = role['role'] if role else 'member'

        conn.close()
        return jsonify({'guild': guild})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ TREASURY ============

@app.route('/api/guild/donate', methods=['POST'])
def donate_to_guild():
    """Закинуть золото в казну"""
    try:
        data = request.json
        tg_id = str(data.get('tg_id'))
        amount = int(data.get('amount', 0))

        if amount < 1:
            return jsonify({'error': 'Invalid amount'}), 400

        conn = get_db()
        cur = conn.cursor()

        cur.execute('SELECT gold, guild_id FROM users WHERE tg_id = ?', (tg_id,))
        user = cur.fetchone()
        if not user or not user['guild_id']:
            conn.close()
            return jsonify({'error': 'Not in guild'}), 400
        if user['gold'] < amount:
            conn.close()
            return jsonify({'error': 'Not enough gold'}), 400

        cur.execute('UPDATE users SET gold = gold - ? WHERE tg_id = ?', (amount, tg_id))
        cur.execute('UPDATE guilds SET treasury = treasury + ? WHERE id = ?', (amount, user['guild_id']))
        cur.execute('UPDATE guild_members SET donated = donated + ? WHERE guild_id = ? AND tg_id = ?', (amount, user['guild_id'], tg_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/guild/give', methods=['POST'])
def give_from_treasury():
    """Лидер выдаёт золото участнику"""
    try:
        data = request.json
        leader_id = str(data.get('tg_id'))
        target_id = str(data.get('target_id'))
        amount = int(data.get('amount', 0))

        if amount < 1:
            return jsonify({'error': 'Invalid amount'}), 400

        conn = get_db()
        cur = conn.cursor()

        cur.execute('SELECT guild_id FROM users WHERE tg_id = ?', (leader_id,))
        user = cur.fetchone()
        if not user or not user['guild_id']:
            conn.close()
            return jsonify({'error': 'Not in guild'}), 400

        guild_id = user['guild_id']

        cur.execute('SELECT leader_id, treasury FROM guilds WHERE id = ?', (guild_id,))
        guild = cur.fetchone()
        if guild['leader_id'] != leader_id:
            conn.close()
            return jsonify({'error': 'Not leader'}), 403
        if guild['treasury'] < amount:
            conn.close()
            return jsonify({'error': 'Not enough in treasury'}), 400

        # Проверяем что target в гильдии
        cur.execute('SELECT tg_id FROM guild_members WHERE guild_id = ? AND tg_id = ?', (guild_id, target_id))
        if not cur.fetchone():
            conn.close()
            return jsonify({'error': 'Target not in guild'}), 400

        cur.execute('UPDATE guilds SET treasury = treasury - ? WHERE id = ?', (amount, guild_id))
        cur.execute('UPDATE users SET gold = gold + ? WHERE tg_id = ?', (amount, target_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/guild/withdraw', methods=['POST'])
def withdraw_from_treasury():
    """Лидер забирает золото себе"""
    try:
        data = request.json
        leader_id = str(data.get('tg_id'))
        amount = int(data.get('amount', 0))

        if amount < 1:
            return jsonify({'error': 'Invalid amount'}), 400

        conn = get_db()
        cur = conn.cursor()

        cur.execute('SELECT guild_id FROM users WHERE tg_id = ?', (leader_id,))
        user = cur.fetchone()
        if not user or not user['guild_id']:
            conn.close()
            return jsonify({'error': 'Not in guild'}), 400

        guild_id = user['guild_id']

        cur.execute('SELECT leader_id, treasury FROM guilds WHERE id = ?', (guild_id,))
        guild = cur.fetchone()
        if guild['leader_id'] != leader_id:
            conn.close()
            return jsonify({'error': 'Not leader'}), 403
        if guild['treasury'] < amount:
            conn.close()
            return jsonify({'error': 'Not enough in treasury'}), 400

        cur.execute('UPDATE guilds SET treasury = treasury - ? WHERE id = ?', (amount, guild_id))
        cur.execute('UPDATE users SET gold = gold + ? WHERE tg_id = ?', (amount, leader_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/guild/kick', methods=['POST'])
def kick_member():
    """Лидер кикает участника"""
    try:
        data = request.json
        leader_id = str(data.get('tg_id'))
        target_id = str(data.get('target_id'))

        conn = get_db()
        cur = conn.cursor()

        cur.execute('SELECT guild_id FROM users WHERE tg_id = ?', (leader_id,))
        user = cur.fetchone()
        if not user or not user['guild_id']:
            conn.close()
            return jsonify({'error': 'Not in guild'}), 400

        guild_id = user['guild_id']

        cur.execute('SELECT leader_id FROM guilds WHERE id = ?', (guild_id,))
        guild = cur.fetchone()
        if guild['leader_id'] != leader_id:
            conn.close()
            return jsonify({'error': 'Not leader'}), 403
        if leader_id == target_id:
            conn.close()
            return jsonify({'error': 'Cannot kick yourself'}), 400

        cur.execute('SELECT level FROM users WHERE tg_id = ?', (target_id,))
        target = cur.fetchone()

        cur.execute('DELETE FROM guild_members WHERE guild_id = ? AND tg_id = ?', (guild_id, target_id))
        cur.execute('UPDATE users SET guild_id = NULL WHERE tg_id = ?', (target_id,))
        cur.execute('UPDATE guilds SET member_count = member_count - 1, total_level = total_level - ? WHERE id = ?', (target['level'] if target else 0, guild_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ START ============
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    print(f"🎮 Tap Royale Server v2 on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
