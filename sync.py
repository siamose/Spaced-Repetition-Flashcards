import os, json, re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from notion_client import Client
from openai import OpenAI

load_dotenv()

openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
notion = Client(auth=os.getenv("NOTION_TOKEN"))
DB_ID = os.getenv("NOTION_DB")


def clean(text) -> str:
    """
    - HTML を除去
    - list/dict/None を安全に文字列へ変換
    - 連続改行 > 2 行 を 2 行に整形
    """
    if text is None:
        return ""
    if isinstance(text, (list, tuple)):
        text = " ".join(map(str, text))
    elif not isinstance(text, str):
        text = str(text)

    try:
        plain = BeautifulSoup(text, "html.parser").get_text()
    except Exception:
        plain = text  # 解析失敗時はそのまま使う

    return re.sub(r"\n{3,}", "\n\n", plain)


def gpt_meta(q: str, a: str) -> dict:
    prompt = f"""以下のQとAを読み、次のJSONで答えて:
{{
"title": "20文字以内の要約",
"topic": ["3語以内トピック"],
"difficulty": "★|★★|★★★"
}}

Q:{q}
A:{a}"""
    res = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return json.loads(res.choices[0].message.content)


def export_pairs() -> list[tuple[str, str]]:
    with open("inbox/conversations.json", encoding="utf-8") as f:
        data = json.load(f)

    pairs: list[tuple[str, str]] = []
    cur_q: str | None = None

    for conv in data:
        for node in conv["mapping"].values():
            # ① message が存在しないノードはスキップ
            message = node.get("message")
            if message is None:
                continue

            role = message.get("author", {}).get("role")
            parts = (message.get("content") or {}).get("parts") or []
            if not parts:
                continue
            txt = parts[0]       # 文字列以外の可能性をclean()で吸収

            if role == "user":
                cur_q = clean(txt)
            elif role == "assistant" and cur_q:
                pairs.append((cur_q, clean(txt)))
                cur_q = None

    return pairs


def send_to_notion(q: str, a: str, meta: dict) -> None:
    notion.pages.create(
        parent={"database_id": DB_ID},
        properties={
            "Title": {"title": [{"text": {"content": meta["title"]}}]},
            "Question": {"rich_text": [{"text": {"content": q}}]},
            "Answer": {"rich_text": [{"text": {"content": a}}]},
            "Topic": {"multi_select": [{"name": t} for t in meta["topic"]]},
            "Difficulty": {"select": {"name": meta["difficulty"]}},
            "Status": {"select": {"name": "🆕 New"}},
            "Last Reviewed": {
                "date": {"start": datetime.now(timezone.utc).isoformat()}
            },
            "Review Interval": {"number": 1},
        },
    )


if __name__ == "__main__":
    for q_text, a_text in export_pairs():
        meta_info = gpt_meta(q_text, a_text)
        send_to_notion(q_text, a_text, meta_info)
        print("→", meta_info["title"])
