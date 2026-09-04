#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""نشر منشورات قناة Telegram العامة @reuters_Ar إلى قناة أخرى عبر Bot API.

لا يستخدم Userbot: يقرأ صفحة المعاينة العامة t.me/s ثم ينشر النص والوسائط المتاحة.
"""

import hashlib
import html
import json
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SOURCE_USERNAME = "reuters_Ar"
SOURCE_URL = f"https://t.me/s/{SOURCE_USERNAME}"
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DESTINATION = os.environ["TELEGRAM_CHANNEL_ID"]
HISTORY_FILE = Path(os.environ.get("HISTORY_FILE", "reuters_ar_history.json"))
MAX_HISTORY = 1000
TIMEOUT = 30

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ReutersArPublisher/1.0; +https://t.me/reuters_Ar)"
}


def load_history() -> set[str]:
    if not HISTORY_FILE.exists():
        return set()
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else [])
    except (OSError, ValueError):
        return set()


def save_history(history: set[str]) -> None:
    values = list(history)[-MAX_HISTORY:]
    HISTORY_FILE.write_text(
        json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def clean_text(value: str) -> str:
    value = re.sub(
        r"(?:\n+ـ{5,})?\n*للاشتراك(?: بالقناة)? عبر تيليجرام:?\s*\n+"
        r"https?://t\.me/reuters_Ar\S*.*$",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    lines = []
    for line in value.splitlines():
        stripped = line.strip()
        # منشورات Reuters العربية تتبع النص العربي بنسخة إنجليزية.
        # نوقف القراءة عند أول سطر إنجليزي، مع الإبقاء على الرموز السابقة له.
        if re.match(r"^[^\u0600-\u06FF\n]*[A-Za-z]", stripped):
            break
        if re.search(r"(?:رويترز|Reuters)\s*[•·-]", stripped):
            break
        if stripped.startswith("https://t.me/"):
            break
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_message_text(text_node) -> str:
    """يحافظ على الرموز بجانب النص ويحوّل br إلى فواصل فعلية."""
    for br in text_node.find_all("br"):
        br.replace_with("\n")
    return clean_text(text_node.get_text("", strip=False))


def fetch_posts() -> list[dict[str, str]]:
    response = requests.get(SOURCE_URL, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    posts = []

    for wrap in soup.select(".tgme_widget_message_wrap"):
        message = wrap.select_one(".tgme_widget_message")
        text_node = wrap.select_one(".tgme_widget_message_text")
        post_link = wrap.select_one(".tgme_widget_message_date")
        if not message or not post_link:
            continue

        data_post = message.get("data-post", "")
        post_id = data_post.rsplit("/", 1)[-1] if data_post else ""
        if not post_id:
            continue

        text = extract_message_text(text_node) if text_node else ""
        url = post_link.get("href", f"https://t.me/{SOURCE_USERNAME}/{post_id}")
        media_url = ""
        media_type = ""
        photo_wrap = wrap.select_one(".tgme_widget_message_photo_wrap")
        if photo_wrap:
            style = photo_wrap.get("style", "")
            match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
            if match:
                media_url = html.unescape(match.group(1))
                media_type = "photo"

        video = wrap.select_one("video")
        if video:
            source = video.select_one("source")
            candidate = video.get("src") or (source.get("src") if source else "")
            if candidate:
                media_url = candidate
                media_type = "video"

        # تجاهل النشرة اليومية والمنشورات التي لا تحتوي نصًا قابلًا للاستخراج.
        if text.startswith("📰 النَّشْرَةُ الإِخْبَارِيَّةُ الشَّامِلَةُ"):
            continue
        if not text:
            continue

        posts.append({
            # معرّف منشور Telegram ثابت؛ نستخدمه حتى لا تعاد منشورات السجل القديم.
            "id": post_id,
            "text": text,
            "url": url,
            "media_url": media_url,
            "media_type": media_type,
        })

    return posts


def format_message(post: dict[str, str]) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", post["text"]) if part.strip()]
    title = paragraphs[0] if paragraphs else post["text"]
    summary = "\n\n".join(paragraphs[1:])

    parts = [f"<b>{html.escape(title)}</b>"]
    if summary:
        # Telegram Bot API يدعم blockquote expandable في HTML.
        parts.append(f"<blockquote expandable>\n{html.escape(summary)}\n</blockquote>")
    parts.append(
        "ــــــــــــــــــــــــــــ\n\n"
        "للاشتراك بالقناة عبر تيليجرام:\n"
        "https://t.me/hasadalyoum"
    )
    return "\n\n".join(parts)


def send_to_telegram(post: dict[str, str]) -> bool:
    message = format_message(post)
    media_url = post.get("media_url", "")
    media_type = post.get("media_type", "")

    if media_url and media_type in {"photo", "video"}:
        try:
            media_response = requests.get(
                media_url, headers=HEADERS, timeout=TIMEOUT, stream=True
            )
            media_response.raise_for_status()
            field = media_type
            endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/send{field.title()}"
            payload = {
                "chat_id": DESTINATION,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if len(message) <= 1024:
                payload["caption"] = message
            response = requests.post(
                endpoint,
                data=payload,
                files={field: (f"post_{post['id']}", media_response.raw)},
                timeout=TIMEOUT,
            )
            if not response.ok or not response.json().get("ok"):
                print(f"Media upload failed: {response.status_code} {response.text}")
                return False
            # Captions have a 1024-character limit; send the full text separately.
            if len(message) > 1024:
                return send_text_message(message)
            return True
        except requests.RequestException as error:
            print(f"Media transfer failed: {error}")
            return False

    return send_text_message(message)


def send_text_message(message: str) -> bool:
    endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        endpoint,
        json={
            "chat_id": DESTINATION,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=TIMEOUT,
    )
    if not response.ok or not response.json().get("ok"):
        print(f"Telegram error: {response.status_code} {response.text}")
        return False
    return True


def main() -> None:
    history = load_history()
    posts = fetch_posts()
    fresh = [post for post in posts if post["id"] not in history]

    if not fresh:
        print("لا توجد منشورات جديدة.")
        return

    # الصفحة مرتبة من الأقدم إلى الأحدث عادةً؛ نرسل بالترتيب الظاهر.
    sent = 0
    for post in fresh:
        if send_to_telegram(post):
            history.add(post["id"])
            sent += 1
            # حفظ السجل فورًا بعد كل إرسال ناجح لتقليل احتمال إعادة النشر
            # إذا توقف تشغيل GitHub Actions بعد إرسال الرسالة.
            save_history(history)

    save_history(history)
    print(f"تم نشر {sent} من {len(fresh)} منشور جديد.")


if __name__ == "__main__":
    main()
