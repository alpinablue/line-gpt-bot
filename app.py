# スマートロックアドバイザー LINEボット
# アイタッチスリムのサポート用公式アカウント向け Webhook サーバー。
# メッセージを受け取り、knowledge.md の内容をもとに OpenAI で回答を生成して返信する。
# Webhook URL は LINE Developers 側の設定に合わせて /callback（/webhook も併設）。

import base64
import hashlib
import hmac
import os
import sys
from collections import defaultdict

import requests
from flask import Flask, abort, request

app = Flask(__name__)

# ---- 設定（すべて環境変数から読む。実値は Render の Environment に設定する） ----
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_PATH = os.environ.get(
    "KNOWLEDGE_PATH", os.path.join(_BASE_DIR, "knowledge.md")
)

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
HISTORY_LIMIT = 10  # 1ユーザーあたり保持する直近の会話ターン数

# ユーザーごとの会話履歴（メモリ内のみ。再起動でリセットされる）
histories = defaultdict(list)

SYSTEM_PROMPT = """あなたは「スマートロックアドバイザー」です。スマートロック製品「アイタッチスリム」のサポートアシスタントとして、LINEでお客様からの問い合わせに答えます。

ルール:
- 回答は、以下に提供される「知識（knowledge）」の内容に基づいてください。
- 丁寧な日本語で、簡潔に答えてください。不具合の相談には、確認・対処の手順を番号付きで分かりやすく示してください。
- 知識に載っていない内容については、推測で答えず、正直に「確認が必要です」と伝えたうえで、製品サポート窓口（ソニックブレイン株式会社 フリーダイヤル 0120-024-669、24時間365日）または担当者へ取り次ぐ旨をご案内ください。
- 製品の仕様・価格・手順を、知識にない形で創作しないでください。
- 危険な作業や分解を促すような案内はしないでください。

知識（knowledge）:
{knowledge}
"""


def load_knowledge():
    """knowledge.md を読み込む。無ければ空文字で続行する。"""
    try:
        with open(KNOWLEDGE_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        app.logger.warning("knowledge file not found: %s（空の知識で続行）", KNOWLEDGE_PATH)
        return ""


KNOWLEDGE = load_knowledge()


def verify_signature(body_bytes, signature):
    """X-Line-Signature を検証する（チャネルシークレットで HMAC-SHA256）。"""
    if not LINE_CHANNEL_SECRET or not signature:
        return False
    mac = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"), body_bytes, hashlib.sha256
    ).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def reply(reply_token, text):
    """LINE の reply API でテキストを返信する。"""
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:5000]}],
    }
    resp = requests.post(LINE_REPLY_URL, headers=headers, json=payload, timeout=15)
    if resp.status_code != 200:
        app.logger.error("LINE reply failed: %s %s", resp.status_code, resp.text)


def ask_ai(user_id, user_text):
    """会話履歴を踏まえて OpenAI に回答を生成させる。"""
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    history = histories[user_id]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(knowledge=KNOWLEDGE)}
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    completion = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.3,
        max_tokens=800,
    )
    answer = completion.choices[0].message.content.strip()

    # 履歴を更新（古いものから捨てて直近 HISTORY_LIMIT ターンだけ残す）
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": answer})
    del history[: max(0, len(history) - HISTORY_LIMIT * 2)]
    return answer


@app.route("/", methods=["GET"])
def health():
    return "OK", 200


@app.route("/callback", methods=["POST"])
@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body_bytes = request.get_data(cache=True)
    if not verify_signature(body_bytes, signature):
        abort(400)

    data = request.get_json(silent=True) or {}
    for event in data.get("events", []):
        reply_token = event.get("replyToken")
        source = event.get("source", {})
        user_id = source.get("userId", "unknown")
        event_type = event.get("type")

        if event_type == "follow":
            if reply_token:
                reply(
                    reply_token,
                    "友だち追加ありがとうございます。スマートロックアドバイザーです。\n"
                    "スマートロック「アイタッチスリム」の使い方や、不具合のご相談にお答えします。\n"
                    "お困りの内容を、文章で送ってください。",
                )
            continue

        if event_type != "message":
            continue

        message = event.get("message", {})
        if message.get("type") != "text":
            if reply_token:
                reply(
                    reply_token,
                    "申し訳ありません、文章でのご質問のみ受け付けています。お困りの内容を文字で送ってください。",
                )
            continue

        user_text = message.get("text", "").strip()
        if not user_text or not reply_token:
            continue

        try:
            answer = ask_ai(user_id, user_text)
        except Exception:
            app.logger.exception("AI call failed")
            answer = (
                "申し訳ありません、ただいま回答を作成できませんでした。"
                "お手数ですが、時間をおいて再度お試しください。"
                "お急ぎの場合は、担当者へ取り次ぎますのでその旨をお知らせください。"
            )
        reply(reply_token, answer)

    return "OK", 200


def check_env():
    """起動時に必須の環境変数の有無を警告する（サービス自体は止めない）。"""
    missing = [
        name
        for name, value in [
            ("LINE_CHANNEL_SECRET", LINE_CHANNEL_SECRET),
            ("LINE_CHANNEL_ACCESS_TOKEN", LINE_CHANNEL_ACCESS_TOKEN),
            ("OPENAI_API_KEY", OPENAI_API_KEY),
        ]
        if not value
    ]
    if missing:
        print(
            f"[startup] 警告：必須の環境変数が未設定です: {', '.join(missing)}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    check_env()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
