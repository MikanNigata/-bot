import discord
import os
import re
import threading
from dotenv import load_dotenv
import psycopg2
import logging
from flask import Flask

# --- ロギング設定 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# --- 設定 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL') # Renderが提供するDBのURL
URL_REGEX = r'''https?://[^
\s<>"()]+|www\.[^
\s<>"()]+'''

# --- Webサーバー機能 (Flask) ---
app = Flask(__name__)

@app.route('/')
def hello_world():
    return 'URL Checker Bot is alive!'

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- データベース関連 (PostgreSQL) ---
def get_db_connection():
    """データベースへの接続を取得する"""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def setup_database():
    """データベースとテーブルを作成する"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS url_log (
                        url TEXT PRIMARY KEY,
                        message_id BIGINT NOT NULL,
                        channel_id BIGINT NOT NULL,
                        guild_id BIGINT NOT NULL,
                        message_url TEXT NOT NULL
                    )
                ''')
        logging.info("PostgreSQLデータベースの準備が完了しました。")
    except Exception as e:
        logging.critical("データベースのセットアップに失敗しました。", exc_info=True)

def find_url_duplicate(url):
    """URLがDBに存在するか確認し、存在すれば元のメッセージURLを返す"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT message_url FROM url_log WHERE url = %s", (url,))
                found = cur.fetchone()
                return found[0] if found else None
    except Exception as e:
        logging.error("DBでの重複チェック中にエラーが発生しました。", exc_info=True)
        return None

def add_url_to_db(url, message):
    """新しいURLをDBに追加する"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO url_log (url, message_id, channel_id, guild_id, message_url) VALUES (%s, %s, %s, %s, %s)",
                            (url, message.id, message.channel.id, message.guild.id, message.jump_url))
    except psycopg2.IntegrityError:
        logging.warning(f"URLの重複登録を試みました（無視）: {url}")
    except Exception as e:
        logging.error("DBへのURL登録中にエラーが発生しました。", exc_info=True)

# --- Discord Bot本体 ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    setup_database()
    logging.info(f'{client.user} (URL Checker) としてログインしました。')

@client.event
async def on_message(message):
    if message.author.bot:
        return

    found_urls = re.findall(URL_REGEX, message.content)
    if not found_urls:
        return

    for url in found_urls:
        loop = client.loop
        duplicate_message_url = await loop.run_in_executor(None, find_url_duplicate, url)

        if duplicate_message_url:
            await message.reply(f"このURLは過去に投稿されています！\n{duplicate_message_url}")
            logging.info(f"重複URLを検出: {url}")
        else:
            await loop.run_in_executor(None, add_url_to_db, url, message)
            logging.info(f"新規URLを登録: {url}")

# --- 実行 ---
if __name__ == "__main__":
    if not TOKEN or not DATABASE_URL:
        logging.critical("エラー: DISCORD_TOKENまたはDATABASE_URLが設定されていません。")
    else:
        # Webサーバーを別スレッドで起動
        web_thread = threading.Thread(target=run_web_server, daemon=True)
        web_thread.start()
        logging.info("Webサーバーが別スレッドで起動しました。")

        # Discord Botをメインスレッドで実行
        try:
            client.run(TOKEN)
        except Exception as e:
            logging.critical("Botの実行に失敗しました。", exc_info=True)