from flask import Flask, request, abort
import openai
import os
import logging
from dotenv import load_dotenv
from models import ChatHistory, session
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
logging.basicConfig(filename='error.log', level=logging.ERROR)
load_dotenv()
openai.api_key = os.getenv('OPENAI_API_KEY')
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        user_message = event.message.text
        system_prompt = """あなたは、株式会社ファイナルのi-Touch Slim取扱説明書に基づいて、親切丁寧にサポートを行うLINEボットです。"""
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        )
        reply_text = response["choices"][0]["message"]["content"]
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        chat = ChatHistory(user_id=event.source.user_id, user_message=user_message, bot_reply=reply_text)
        session.add(chat)
        session.commit()
    except Exception as e:
        logging.error(f'エラー発生: {str(e)}')
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ごめんなさい、内部エラーが発生しました。"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
