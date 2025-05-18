# -*- coding: utf-8 -*-
"""
ChatGPT Export → Notion DB 同期スクリプト（堅牢版）
- 2 000 文字制限を自動分割 children で回避
- OpenAI には response_format=json_object を強制
- Notion セレクト値が存在しなければ自動生成
- 例外が出ても次の Q&A へ続行（学習ログを最大限保存）
"""

import json, os, re, time, warnings
from datetime import datetime, timezone
from typing import List, Tuple

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning, MarkupResemblesLocatorWarning
from dotenv import load_dotenv
from notion_client import Client
from notion_client.helpers import get_id
from notion_client.errors import APIResponseError
from openai import OpenAI, OpenAIError

# ─── 環境変数 ────────────────────────────────────────
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NOTION_TOKEN   = os.getenv("NOTION_TOKEN")
DB_ID          = os.getenv("NOTION_DB")

if not re.fullmatch(r"[0-9a-f]{32}", (DB_ID or "").lower()):
    raise RuntimeError("NOTION_DB は ハイフン無し32桁のデータベースIDを Secrets に設定してください。")


if not all([OPENAI_API_KEY, NOTION_TOKEN, DB_ID]):
    raise RuntimeError("OPENAI_API_KEY / NOTION_TOKEN / NOTION_DB が空です。Secrets を確認してください。")

openai = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_TOKEN)

# ─── 定数 ───────────────────────────────────────────
MAX_CHARS = 1990                # 2 000 未満に安全マージン
DIFF_VALUES = {"★", "★★", "★★★"}
STATUS_NEW  = "🆕 New"

# ─── Warn フィルタ（BeautifulSoup）──────────────────
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

# ─── Utility ────────────────────────────────────────
def clean(text) -> str:
    """リスト・dict・URL など何でも安全にプレーン化し、改行を整理"""
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

# ─── OpenAI でメタ情報生成 ───────────────────────────
def gpt_meta(q: str, a: str) -> dict:
    prompt = f"""以下のQとAを読み、次の JSON で答えて:
{{
"title": "20文字以内の要約",
"topic": ["3語以内トピック"],
"difficulty": "★|★★|★★★"
}}

Q:{q}
A:{a}"""
    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            timeout=30,
        )
        meta = json.loads(res.choices[0].message.content)
    except (OpenAIError, json.JSONDecodeError) as e:
        # フォールバック：質問冒頭20文字・★★ に固定
        meta = {"title": q[:20], "topic": [], "difficulty": "★★"}
        print("⚠ meta 生成失敗:", e)
    # 値バリデーション
    meta["difficulty"] = meta.get("difficulty", "★★")
    if meta["difficulty"] not in DIFF_VALUES:
        meta["difficulty"] = "★★"
    meta["topic"] = meta.get("topic", [])[:5]
    return meta

# ─── Q&A 抽出 ────────────────────────────────────────
def export_pairs() -> List[Tuple[str, str]]:
    with open("inbox/conversations.json", encoding="utf-8") as f:
        data = json.load(f)

    pairs, cur_q = [], None
    for conv in data:
        for node in conv.get("mapping", {}).values():
            message = node.get("message") or {}
            role = message.get("author", {}).get("role")
            parts = (message.get("content") or {}).get("parts") or []
            txt = parts[0] if parts else ""
            if role == "user":
                cur_q = clean(txt)
            elif role == "assistant" and cur_q:
                pairs.append((cur_q, clean(txt)))
                cur_q = None
    return pairs

# ─── Notion へ送信 ───────────────────────────────────
def send_to_notion(q: str, a: str, meta: dict):
    # 1) 先頭チャンクでページ作成
    page_props = {
        "Title": {"title":[{"text":{"content": meta["title"]}}]},
        "Question":{"rich_text":[{"text":{"content": q[:MAX_CHARS]}}]},
        "Answer":  {"rich_text":[{"text":{"content": a[:MAX_CHARS]}}]},
        "Difficulty":{"select":{"name": meta["difficulty"]}},
        "Status": {"select":{"name": STATUS_NEW}},
        "Last Reviewed":{"date":{"start": datetime.now(timezone.utc).isoformat()}},
        "Review Interval":{"number":1},
    }
    # Topic (multi-select) は空配列可
    if meta["topic"]:
        page_props["Topic"] = {"multi_select":[{"name": t} for t in meta["topic"]]}
    try:
        page = notion.pages.create(parent={"database_id": get_id(DB_ID)}, properties=page_props)
    except APIResponseError as e:
        print("❌ページ作成失敗:", e.message)
        return

    # 2) 残りを children で分割追記
    remainder = a[MAX_CHARS:]
    if remainder:
        chunks = [remainder[i:i+MAX_CHARS] for i in range(0, len(remainder), MAX_CHARS)]
        children = [{"object":"block","type":"paragraph",
                     "paragraph":{"rich_text":[{"type":"text","text":{"content":ch}}]}}
                    for ch in chunks]
        # 最大 50/chunk に満たないはずだが念のため分割
        for i in range(0, len(children), 50):
            notion.blocks.children.append(block_id=page["id"], children=children[i:i+50])


# ─── Main ────────────────────────────────────────────
if __name__ == "__main__":
    for q, a in export_pairs():
        try:
            meta = gpt_meta(q, a)
            send_to_notion(q, a, meta)
            print("✅", meta["title"])
            time.sleep(0.5)               # 軽いレート制御
        except Exception as e:
            print("⚠ 1件スキップ:", e)
