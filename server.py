# server_postgres.py
# Tap Royale API v3 — PostgreSQL (users + guilds + treasury)

import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
CORS(app)

# ================== CONFIG ==================

# Пример DATABASE_URL:
# postgres://user:password@host:port/dbname
DATABASE_URL = os.getenv("postgresql://postgres:gTDEDTSzHKANjsFdTicItOHsDHEQXXUp@postgres.railway.internal:5432/railway")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# ================== DB HELPERS ==================

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            tg_id TEXT UNIQUE NOT NULL,
            nickname TEXT DEFAULT 'Player',
            gold BIGINT DEFAULT 0,
            gems BIGINT DEFAULT 0,
            level INT DEFAULT 1,
            total_taps BIGINT DEFAULT 0,
            referrer_id TEXT,
            referral_count INT DEFAULT 0,
            guild_id INT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # guilds
    cur.execute("""
        CREATE TABLE IF NOT EXISTS guilds (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            leader_id TEXT NOT NULL,
            treasury BIGINT DEFAULT 0,
            total_level BIGINT DEFAULT 0,
            member_count INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # guild_members
    cur.execute("""
        CREATE TABLE IF NOT EXISTS guild_members (
            id SERIAL PRIMARY KEY,
            guild_id INT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
            tg_id TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            donated BIGINT DEFAULT 0,
            joined_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(guild_id, tg_id)
        );
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_gm_guild ON guild_members(guild_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_guild ON users(guild_id);")

    conn.commit()
    cur.close()
    conn.close()

# ================== BASIC ==================

@app.route("/")
def home():
    return jsonify({"name": "Tap Royale API", "version": "3.0", "db": "postgres"})

# ================== USER / SYNC ==================

@app.route("/api/sync", methods=["POST"])
def sync():
    try:
        data = request.get_json(force=True)
        tg_id = str(data.get("tg_id"))
        if not tg_id:
            return jsonify({"error": "tg_id required"}), 400

        nickname = data.get("nickname")
        gold = int(data.get("gold", 0))
        gems = int(data.get("gems", 0))
        level = int(data.get("level", 1))
        taps = int(data.get("totalTaps", 0))

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT * FROM users WHERE tg_id = %s", (tg_id,))
        user = cur.fetchone()

        if user:
            cur.execute(
                """
                UPDATE users SET
                    nickname = COALESCE(%s, nickname),
                    gold = GREATEST(gold, %s),
                    gems = GREATEST(gems, %s),
                    level = GREATEST(level, %s),
                    total_taps = GREATEST(total_taps, %s)
                WHERE tg_id = %s
                """,
                (nickname, gold, gems, level, taps, tg_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO users (tg_id, nickname, gold, gems, level, total_taps)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (tg_id, nickname or "Player", gold, gems, level, taps),
            )

        conn.commit()

        cur.execute("SELECT referral_count FROM users WHERE tg_id = %s", (tg_id,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        return jsonify({"success": True, "referrals": user["referral_count"] if user else 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================== REFERRAL ==================

@app.route("/api/referral", methods=["POST"])
def referral():
    try:
        data = request.get_json(force=True)
        new_id = str(data.get("new_user_id"))
        ref_id = str(data.get("referrer_id"))

        if not new_id or not ref_id or new_id == ref_id:
            return jsonify({"success": False}), 400

        conn = get_db()
        cur = conn.cursor()

        # уже есть реферер?
        cur.execute("SELECT referrer_id FROM users WHERE tg_id = %s", (new_id,))
        row = cur.fetchone()
        if row and row["referrer_id"]:
            cur.close()
            conn.close()
            return jsonify({"success": False, "reason": "already_referred"})

        # создаём пользователей, если нет
        cur.execute("INSERT INTO users (tg_id) VALUES (%s) ON CONFLICT (tg_id) DO NOTHING", (ref_id,))
        cur.execute("INSERT INTO users (tg_id) VALUES (%s) ON CONFLICT (tg_id) DO NOTHING", (new_id,))

        # ставим реферера и даём бонус
        cur.execute(
            """
            UPDATE users
            SET referrer_id = %s, gold = gold + 500, gems = gems + 3
            WHERE tg_id = %s AND referrer_id IS NULL
            """,
            (ref_id, new_id),
        )
        updated = cur.rowcount

        if updated > 0:
            cur.execute(
                """
                UPDATE users
                SET referral_count = referral_count + 1, gold = gold + 500, gems = gems + 3
                WHERE tg_id = %s
                """,
                (ref_id,),
            )

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True, "bonus": {"gold": 500, "gems": 3}})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================== LEADERBOARD ==================

@app.route("/api/leaderboard", methods=["GET"])
def leaderboard():
    try:
        lb_type = request.args.get("type", "level")
        if lb_type == "gold":
            order = "gold DESC"
        elif lb_type == "refs":
            order = "referral_count DESC"
        else:
            order = "level DESC, gold DESC"

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT tg_id, nickname, level, gold, referral_count AS referrals
            FROM users
            ORDER BY {order}
            LIMIT 50
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================== GUILDS ==================

@app.route("/api/guilds", methods=["GET"])
def get_guilds():
    """Список всех гильдий (для топа и списка)."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT g.*, u.nickname AS leader_name
            FROM guilds g
            LEFT JOIN users u ON g.leader_id = u.tg_id
            ORDER BY g.total_level DESC
            LIMIT 50
            """
        )
        guilds = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify(guilds)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/guild/create", methods=["POST"])
def create_guild():
    """Создать гильдию (5 гемов)."""
    try:
        data = request.get_json(force=True)
        tg_id = str(data.get("tg_id"))
        name = (data.get("name") or "").strip()

        if not tg_id or len(name) < 2:
            return jsonify({"error": "Invalid data"}), 400

        conn = get_db()
        cur = conn.cursor()

        # проверяем юзера
        cur.execute("SELECT gems, guild_id, level FROM users WHERE tg_id = %s", (tg_id,))
        user = cur.fetchone()
        if not user:
            cur.close()
            conn.close()
            return jsonify({"error": "User not found"}), 404

        if user["gems"] < 5:
            cur.close()
            conn.close()
            return jsonify({"error": "Not enough gems"}), 400

        if user["guild_id"]:
            cur.close()
            conn.close()
            return jsonify({"error": "Already in guild"}), 400

        # имя занято?
        cur.execute("SELECT id FROM guilds WHERE name = %s", (name,))
        if cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"error": "Name taken"}), 400

        # создаём
        cur.execute("UPDATE users SET gems = gems - 5 WHERE tg_id = %s", (tg_id,))
        cur.execute(
            """
            INSERT INTO guilds (name, leader_id, total_level)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (name, tg_id, user["level"]),
        )
        gid = cur.fetchone()["id"]

        cur.execute("UPDATE users SET guild_id = %s WHERE tg_id = %s", (gid, tg_id))
        cur.execute(
            """
            INSERT INTO guild_members (guild_id, tg_id, role)
            VALUES (%s, %s, 'leader')
            """,
            (gid, tg_id),
        )

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True, "guild_id": gid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/guild/join", methods=["POST"])
def join_guild():
    try:
        data = request.get_json(force=True)
        tg_id = str(data.get("tg_id"))
        guild_id = int(data.get("guild_id"))

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT guild_id, level FROM users WHERE tg_id = %s", (tg_id,))
        user = cur.fetchone()
        if not user:
            cur.close()
            conn.close()
            return jsonify({"error": "User not found"}), 404

        if user["guild_id"]:
            cur.close()
            conn.close()
            return jsonify({"error": "Already in guild"}), 400

        cur.execute("SELECT * FROM guilds WHERE id = %s", (guild_id,))
        guild = cur.fetchone()
        if not guild:
            cur.close()
            conn.close()
            return jsonify({"error": "Guild not found"}), 404

        if guild["member_count"] >= 20:
            cur.close()
            conn.close()
            return jsonify({"error": "Guild full"}), 400

        cur.execute("UPDATE users SET guild_id = %s WHERE tg_id = %s", (guild_id, tg_id))
        cur.execute(
            """
            INSERT INTO guild_members (guild_id, tg_id, role)
            VALUES (%s, %s, 'member')
            """,
            (guild_id, tg_id),
        )
        cur.execute(
            """
            UPDATE guilds
            SET member_count = member_count + 1,
                total_level = total_level + %s
            WHERE id = %s
            """,
            (user["level"], guild_id),
        )

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/guild/leave", methods=["POST"])
def leave_guild():
    try:
        data = request.get_json(force=True)
        tg_id = str(data.get("tg_id"))

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT guild_id, level FROM users WHERE tg_id = %s", (tg_id,))
        user = cur.fetchone()
        if not user or not user["guild_id"]:
            cur.close()
            conn.close()
            return jsonify({"error": "Not in guild"}), 400

        guild_id = user["guild_id"]

        cur.execute("SELECT leader_id FROM guilds WHERE id = %s", (guild_id,))
        guild = cur.fetchone()
        if not guild:
            cur.close()
            conn.close()
            return jsonify({"error": "Guild not found"}), 404

        # если лидер — передать лидерство или удалить гильдию
        if guild["leader_id"] == tg_id:
            cur.execute(
                """
                SELECT tg_id
                FROM guild_members
                WHERE guild_id = %s AND tg_id <> %s
                ORDER BY joined_at ASC
                LIMIT 1
                """,
                (guild_id, tg_id),
            )
            new_leader = cur.fetchone()
            if new_leader:
                cur.execute(
                    "UPDATE guilds SET leader_id = %s WHERE id = %s",
                    (new_leader["tg_id"], guild_id),
                )
                cur.execute(
                    """
                    UPDATE guild_members
                    SET role = 'leader'
                    WHERE guild_id = %s AND tg_id = %s
                    """,
                    (guild_id, new_leader["tg_id"]),
                )
            else:
                # никого не осталось — удаляем гильдию
                cur.execute("DELETE FROM guilds WHERE id = %s", (guild_id,))

        cur.execute("DELETE FROM guild_members WHERE guild_id = %s AND tg_id = %s", (guild_id, tg_id))
        cur.execute("UPDATE users SET guild_id = NULL WHERE tg_id = %s", (tg_id,))
        cur.execute(
            """
            UPDATE guilds
            SET member_count = member_count - 1,
                total_level = total_level - %s
            WHERE id = %s
            """,
            (user["level"], guild_id),
        )

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/guild/my", methods=["GET"])
def my_guild():
    try:
        tg_id = request.args.get("tg_id")
        if not tg_id:
            return jsonify({"error": "tg_id required"}), 400

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT guild_id FROM users WHERE tg_id = %s", (tg_id,))
        user = cur.fetchone()
        if not user or not user["guild_id"]:
            cur.close()
            conn.close()
            return jsonify({"guild": None})

        gid = user["guild_id"]
        cur.execute("SELECT * FROM guilds WHERE id = %s", (gid,))
        guild = cur.fetchone()
        if not guild:
            cur.close()
            conn.close()
            return jsonify({"guild": None})

        cur.execute(
            """
            SELECT gm.*, u.nickname, u.level
            FROM guild_members gm
            JOIN users u ON gm.tg_id = u.tg_id
            WHERE gm.guild_id = %s
            ORDER BY gm.role DESC, gm.donated DESC
            """,
            (gid,),
        )
        members = cur.fetchall()

        cur.execute(
            "SELECT role FROM guild_members WHERE guild_id = %s AND tg_id = %s",
            (gid, tg_id),
        )
        r = cur.fetchone()
        my_role = r["role"] if r else "member"

        cur.close()
        conn.close()

        guild["members"] = members
        guild["my_role"] = my_role

        return jsonify({"guild": guild})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================== TREASURY ==================

@app.route("/api/guild/donate", methods=["POST"])
def donate():
    try:
        data = request.get_json(force=True)
        tg_id = str(data.get("tg_id"))
        amount = int(data.get("amount", 0))
        if amount < 1:
            return jsonify({"error": "Invalid amount"}), 400

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT gold, guild_id FROM users WHERE tg_id = %s", (tg_id,))
        user = cur.fetchone()
        if not user or not user["guild_id"]:
            cur.close()
            conn.close()
            return jsonify({"error": "Not in guild"}), 400

        if user["gold"] < amount:
            cur.close()
            conn.close()
            return jsonify({"error": "Not enough gold"}), 400

        gid = user["guild_id"]

        cur.execute("UPDATE users SET gold = gold - %s WHERE tg_id = %s", (amount, tg_id))
        cur.execute("UPDATE guilds SET treasury = treasury + %s WHERE id = %s", (amount, gid))
        cur.execute(
            """
            UPDATE guild_members
            SET donated = donated + %s
            WHERE guild_id = %s AND tg_id = %s
            """,
            (amount, gid, tg_id),
        )

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/guild/give", methods=["POST"])
def give():
    """Лидер выдаёт золото из казны участнику."""
    try:
        data = request.get_json(force=True)
        leader_id = str(data.get("tg_id"))
        target_id = str(data.get("target_id"))
        amount = int(data.get("amount", 0))
        if amount < 1:
            return jsonify({"error": "Invalid amount"}), 400

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT guild_id FROM users WHERE tg_id = %s", (leader_id,))
        user = cur.fetchone()
        if not user or not user["guild_id"]:
            cur.close()
            conn.close()
            return jsonify({"error": "Not in guild"}), 400

        gid = user["guild_id"]

        cur.execute("SELECT leader_id, treasury FROM guilds WHERE id = %s", (gid,))
        guild = cur.fetchone()
        if guild["leader_id"] != leader_id:
            cur.close()
            conn.close()
            return jsonify({"error": "Not leader"}), 403

        if guild["treasury"] < amount:
            cur.close()
            conn.close()
            return jsonify({"error": "Not enough in treasury"}), 400

        # target в гильдии?
        cur.execute("SELECT 1 FROM guild_members WHERE guild_id = %s AND tg_id = %s", (gid, target_id))
        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"error": "Target not in guild"}), 400

        cur.execute("UPDATE guilds SET treasury = treasury - %s WHERE id = %s", (amount, gid))
        cur.execute("UPDATE users SET gold = gold + %s WHERE tg_id = %s", (amount, target_id))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/guild/withdraw", methods=["POST"])
def withdraw():
    """Лидер забирает золото из казны себе."""
    try:
        data = request.get_json(force=True)
        leader_id = str(data.get("tg_id"))
        amount = int(data.get("amount", 0))
        if amount < 1:
            return jsonify({"error": "Invalid amount"}), 400

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT guild_id FROM users WHERE tg_id = %s", (leader_id,))
        user = cur.fetchone()
        if not user or not user["guild_id"]:
            cur.close()
            conn.close()
            return jsonify({"error": "Not in guild"}), 400

        gid = user["guild_id"]

        cur.execute("SELECT leader_id, treasury FROM guilds WHERE id = %s", (gid,))
        guild = cur.fetchone()
        if guild["leader_id"] != leader_id:
            cur.close()
            conn.close()
            return jsonify({"error": "Not leader"}), 403

        if guild["treasury"] < amount:
            cur.close()
            conn.close()
            return jsonify({"error": "Not enough in treasury"}), 400

        cur.execute("UPDATE guilds SET treasury = treasury - %s WHERE id = %s", (amount, gid))
        cur.execute("UPDATE users SET gold = gold + %s WHERE tg_id = %s", (amount, leader_id))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/guild/kick", methods=["POST"])
def kick():
    """Лидер кикает участника."""
    try:
        data = request.get_json(force=True)
        leader_id = str(data.get("tg_id"))
        target_id = str(data.get("target_id"))

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT guild_id FROM users WHERE tg_id = %s", (leader_id,))
        user = cur.fetchone()
        if not user or not user["guild_id"]:
            cur.close()
            conn.close()
            return jsonify({"error": "Not in guild"}), 400

        gid = user["guild_id"]

        cur.execute("SELECT leader_id FROM guilds WHERE id = %s", (gid,))
        guild = cur.fetchone()
        if guild["leader_id"] != leader_id:
            cur.close()
            conn.close()
            return jsonify({"error": "Not leader"}), 403

        if leader_id == target_id:
            cur.close()
            conn.close()
            return jsonify({"error": "Cannot kick yourself"}), 400

        cur.execute("SELECT level FROM users WHERE tg_id = %s", (target_id,))
        target = cur.fetchone()

        cur.execute("DELETE FROM guild_members WHERE guild_id = %s AND tg_id = %s", (gid, target_id))
        cur.execute("UPDATE users SET guild_id = NULL WHERE tg_id = %s", (target_id,))
        cur.execute(
            """
            UPDATE guilds
            SET member_count = member_count - 1,
                total_level = total_level - %s
            WHERE id = %s
            """,
            (target["level"] if target else 0, gid),
        )

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================== START ==================

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
