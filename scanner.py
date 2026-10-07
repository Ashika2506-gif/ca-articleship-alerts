import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests


BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = "state.json"

# Initial search sources.
# We can add dedicated CA-firm career pages later.
SEARCHES = [
    "CA articleship vacancy Delhi",
    "CA articleship vacancy Gurgaon",
    "CA articleship vacancy Noida",
    "CA articleship vacancy India",
]

# Only alert for posts containing these terms.
INCLUDE_TERMS = [
    "articleship",
    "article trainee",
    "ca articleship",
    "chartered accountant trainee",
]

# Avoid obvious irrelevant results.
EXCLUDE_TERMS = [
    "course",
    "coaching",
    "classes",
    "exam result",
    "salary",
]


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"seen": []}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": []}


def save_state(state):
    # Keep the state file small.
    state["seen"] = state.get("seen", [])[-2000:]

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def matches_vacancy(title, description):
    text = f"{title} {description}".lower()

    has_include = any(term in text for term in INCLUDE_TERMS)
    has_exclude = any(term in text for term in EXCLUDE_TERMS)

    return has_include and not has_exclude


def parse_date(entry):
    date_text = (
        entry.findtext("pubDate")
        or entry.findtext("{http://purl.org/dc/elements/1.1/}date")
        or ""
    )

    # RSS dates vary between sources.
    # If parsing fails, treat the item as recent.
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(date_text)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        return datetime.now(timezone.utc)


def get_feed_url(search):
    return (
        "https://news.google.com/rss/search?q="
        + quote(search)
        + "&hl=en-IN&gl=IN&ceid=IN:en"
    )


def fetch_feed(search):
    url = get_feed_url(search)

    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": "CA-Articleship-Alert-Bot/1.0"
        },
    )

    response.raise_for_status()

    root = ET.fromstring(response.content)

    results = []

    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title"))
        link = clean_text(item.findtext("link"))
        description = clean_text(item.findtext("description"))
        guid = clean_text(item.findtext("guid")) or link

        published = parse_date(item)

        results.append(
            {
                "id": guid,
                "title": title,
                "link": link,
                "description": description,
                "published": published,
                "search": search,
            }
        )

    return results


def send_telegram(item):
    message = (
        "🔔 NEW CA ARTICLESHiP VACANCY\n\n"
        f"🏢 {item['title']}\n\n"
        f"📍 Search: {item['search']}\n"
        f"🕐 Found: {datetime.now().strftime('%d %b %Y, %I:%M %p')}\n\n"
        f"🔗 Apply / View:\n{item['link']}"
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )

    response.raise_for_status()


def main():
    state = load_state()
    seen = set(state.get("seen", []))

    all_items = []

    for search in SEARCHES:
        try:
            items = fetch_feed(search)
            all_items.extend(items)
            print(f"{search}: {len(items)} results")

        except Exception as e:
            print(f"ERROR scanning '{search}': {e}")

    # Remove duplicate items from multiple searches.
    unique_items = {}

    for item in all_items:
        unique_items[item["id"]] = item

    # Only consider recent results.
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)

    new_items = []

    for item in unique_items.values():

        if item["id"] in seen:
            continue

        if item["published"] < cutoff:
            continue

        if not matches_vacancy(
            item["title"],
            item["description"]
        ):
            continue

        new_items.append(item)

    # Oldest first so alerts arrive in chronological order.
    new_items.sort(key=lambda x: x["published"])

    print(f"New matching vacancies: {len(new_items)}")

    for item in new_items[:10]:
        try:
            send_telegram(item)
            print(f"Alert sent: {item['title']}")

            seen.add(item["id"])

        except Exception as e:
            print(f"Telegram error: {e}")

    # Mark all scanned matching items as seen,
    # even if they were already processed in another search.
    for item in unique_items.values():
        if matches_vacancy(
            item["title"],
            item["description"]
        ):
            seen.add(item["id"])

    state["seen"] = list(seen)
    save_state(state)


if __name__ == "__main__":
    main()
