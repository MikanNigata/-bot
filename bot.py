import discord
from discord.ext import commands
import os
import re
import threading
from dotenv import load_dotenv
import psycopg2
import logging
from flask import Flask
import datetime

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
DATABASE_URL = os.getenv('DATABASE_URL')
URL_REGEX = r'https?://[^\n\s<>]+' # 正規表現を修正

# --- Discordボットの準備 ---
# メッセージ内容を読み取るためにIntentsの設定が必要です
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True 

bot = commands.Bot(command_prefix='!', intents=intents)

# --- データベース関連の処理 ---
def init_db():
    """データベースの初期化（テーブルが存在しない場合のみ作成）"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        # テーブル名やカラム名はご自身の環境に合わせてください
        cur.execute('''
            CREATE TABLE IF NOT EXISTS posted_urls (
                id SERIAL PRIMARY KEY,
                url TEXT NOT NULL,
                message_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                author_id BIGINT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        conn.commit()
        cur.close()
        conn.close()
        logging.info("データベースの初期化が完了しました。")
    except psycopg2.Error as e:
        logging.error(f"データベース初期化エラー: {e}")

# --- イベントリスナー ---
@bot.event
async def on_ready():
    """ボットが起動したときに呼び出される"""
    logging.info(f'{bot.user.name} としてログインしました。')
    init_db() # 起動時にDBを初期化

@bot.event
async def on_message(message):
    """メッセージが投稿されたときに呼び出される"""
    if message.author == bot.user:
        return

    # URLを正規表現で検索
    urls = re.findall(URL_REGEX, message.content)
    if urls:
        try:
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()

            for url in urls:
                # データベースに同じURLが存在するか確認
                # テーブル名やカラム名はご自身の環境に合わせてください
                cur.execute("SELECT url FROM posted_urls WHERE url = %s", (url,))
                result = cur.fetchone()

                if result:
                    # 既に存在する場合
                    logging.info(f"重複したURLが投稿されました: {url}")
                    await message.channel.send(f"このURLは過去に投稿されています: {url}")
                else:
                    # 存在しない場合、DBに保存
                    # テーブル名やカラム名はご自身の環境に合わせてください
                    cur.execute(
                        "INSERT INTO posted_urls (url, message_id, channel_id, guild_id, author_id) VALUES (%s, %s, %s, %s, %s)",
                        (url, message.id, message.channel.id, message.guild.id, message.author.id)
                    )
                    conn.commit()
                    logging.info(f"新しいURLをデータベースに保存しました: {url}")

            cur.close()
            conn.close()
        except psycopg2.Error as e:
            logging.error(f"データベース処理エラー: {e}")

    await bot.process_commands(message) # 他のコマンドを処理するために必要

# --- コマンドの追加 ---
@bot.command(name='show_urls')
async def show_urls(ctx, start_date_str: str, end_date_str: str):
    """
    指定された期間に投稿されたURLをデータベースから検索して表示します。
    日付の形式は YYYY-MM-DD で指定してください。
    例: !show_urls 2025-01-01 2025-01-31
    """
    try:
        # 文字列をdatetimeオブジェクトに変換
        start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d')
        # 終了日はその日の終わりまでを含むように調整
        end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d') + datetime.timedelta(days=1)
    except ValueError:
        await ctx.send("日付の形式が正しくありません。`YYYY-MM-DD` 形式で指定してください。")
        return

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # 期間を指定してURLとタイムスタンプを取得するSQLクエリ
        # !!! ここの 'posted_urls' をご自身のテーブル名に変更してください !!!
        cur.execute(
            "SELECT url, created_at FROM posted_urls WHERE created_at >= %s AND created_at < %s ORDER BY created_at DESC",
            (start_date, end_date)
        )
        results = cur.fetchall()
        
        cur.close()
        conn.close()

        if not results:
            await ctx.send(f"`{start_date.strftime('%Y-%m-%d')}` から `{end_date_str}` までの期間に投稿されたURLはありませんでした。")
            return

        # 結果を整形してメッセージを作成
        # Discordのメッセージ長制限(2000文字)を考慮
        response_message = f"**{start_date.strftime('%Y-%m-%d')} から {end_date_str} までのURL一覧**\n\n"
        for row in results:
            url, timestamp = row
            # タイムスタンプのフォーマットを整える
            formatted_timestamp = timestamp.strftime('%Y-%m-%d %H:%M')
            entry = f"[{formatted_timestamp}] {url}\n"

            if len(response_message) + len(entry) > 2000:
                await ctx.send(response_message)
                response_message = "" # メッセージをリセット
            
            response_message += entry
        
        if response_message:
            await ctx.send(response_message)

    except psycopg2.Error as e:
        logging.error(f"データベースエラー: {e}")
        await ctx.send("データベースへの接続または検索中にエラーが発生しました。")
    except Exception as e:
        logging.error(f"予期せぬエラー: {e}")
        await ctx.send("コマンドの処理中に予期せぬエラーが発生しました。")


# --- Flaskを使ったWebサーバー（Keep-Alive用） ---
# GlitchやReplitなどでホスティングする場合に利用
app = Flask(__name__)

@app.route('/')
def hello_world():
    return 'Bot is running!'

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# --- ボットの実行 ---
if __name__ == "__main__":
    # Flaskを別スレッドで実行
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Discordボットを実行
    bot.run(TOKEN)