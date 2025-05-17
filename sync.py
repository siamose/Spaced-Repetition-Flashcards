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

def clean(text: str) -> str:
# HTML → プレーン, 改行畳み
return re.sub(r'\n{3,}', '\n\n',
BeautifulSoup(text, "html.parser").get_text())

def gpt_meta(q: str, a: str):
prompt = f"""以下のQとAを読み、次のJSONで答えて:
{{
"title": "20文字以内の要約",
"topic": ["3語以内トピック"],
"difficulty": "★|★★|★★★"
}}

Q:{q}\nA:{a}"""
res = openai.chat.completions.create(
model="gpt-4o-mini",
messages=[{"role":"user","content":prompt}],
temperature=0.2
)
return json.loads(res.choices[0].message.content)

def export_pairs():
with open("inbox/conversations.json", encoding="utf-8") as f:
data = json.load(f)
pairs, cur_q = [], None
for conv in data:
for node in conv["mapping"].values():
role = node.get("author", {}).get("role")
txt = node.get("message", {}).get("content", {}).get("parts", [""])[0]
if role == "user":
cur_q = clean(txt)
elif role == "assistant" and cur_q:
pairs.append((cur_q, clean(txt)))
cur_q = None
return pairs

def send_to_notion(q, a, meta):
notion.pages.create(parent={"database_id": DB_ID}, properties={
"Title":{"title":[{"text":{"content":meta}}]},
"Question":{"rich_text":[{"text":{"content":q}}]},
"Answer":{"rich_text":[{"text":{"content":a}}]},
"Topic":{"multi_select":[{"name":t} for t in meta["topic"]]},
"Difficulty":{"select":{"name":meta}},
"Status":{"select":{"name":"🆕 New"}},
"Last Reviewed":{"date":{"start":datetime.now(timezone.utc).isoformat()}},
"Review Interval":{"number":1},
})

if name == "main":
for q, a in export_pairs():
meta = gpt_meta(q, a)
send_to_notion(q, a, meta)
print("→", meta["title"])