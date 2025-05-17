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

MAX_CHARS = 2000  # Notion rich_text の上限

#  Utility 
def clean(text) -> str:
    if text is None:
        return ""
    if isinstance(text, (list, tuple)):
        text = " ".join(map(str, text))
    elif not isinstance(text, str):
        text = str(text)
    try:
        plain = BeautifulSoup(text, "html.parser").get_text()
    except Exception:
        plain = text
    return re.sub(r"\n{3,}", "\n\n", plain)

#  GPT でメタ情報生成 
def gpt_meta(q: str, a: str) -> dict:
    prompt = f"""以下のQとAを読み、次のJSONで答えて:
{{
"title": "20文字以内の要約",
"topic": ["3語以内トピック"],
"difficulty": "||"
}}

Q:{q}
A:{a}"""
    res = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return json.loads(res.choices[0].message.content)

#  Export JSON  Q&A ペア 
def export_pairs():
    with open("inbox/conversations.json", encoding="utf-8") as f:
        data = json.load(f)

    pairs, cur_q = [], None
    for conv in data:
        for node in conv["mapping"].values():
            message = node.get("message")
            if message is None:
                continue
            role = message.get("author", {}).get("role")
            parts = (message.get("content") or {}).get("parts") or []
            if not parts:
                continue
            txt = parts[0]

            if role == "user":
                cur_q = clean(txt)
            elif role == "assistant" and cur_q:
                pairs.append((cur_q, clean(txt)))
                cur_q = None
    return pairs

#  Notion へ送信（children 分割版） 
def send_to_notion(q: str, a: str, meta: dict):
    first_chunk = a[:MAX_CHARS]

    page = notion.pages.create(
        parent={"database_id": DB_ID},
        properties={
            "Title": {"title": [{"text": {"content": meta["title"]}}]},
            "Question": {"rich_text": [{"text": {"content": q[:MAX_CHARS]}}]},
            "Answer":   {"rich_text": [{"text": {"content": first_chunk}}]},
            "Topic":    {"multi_select": [{"name": t} for t in meta["topic"]]},
            "Difficulty": {"select": {"name": meta["difficulty"]}},
            "Status":   {"select": {"name": "?? New"}},
            "Last Reviewed": {
                "date": {"start": datetime.now(timezone.utc).isoformat()}
            },
            "Review Interval": {"number": 1},
        },
    )

    remainder = a[MAX_CHARS:]
    if remainder:
        chunks = [remainder[i:i+MAX_CHARS] for i in range(0, len(remainder), MAX_CHARS)]
        children = [{
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": ch}}]
            }
        } for ch in chunks]
        notion.blocks.children.append(block_id=page["id"], children=children)

#  Main 
if __name__ == "__main__":
    for q_text, a_text in export_pairs():
        meta_info = gpt_meta(q_text, a_text)
        send_to_notion(q_text, a_text, meta_info)
        print("", meta_info["title"])
