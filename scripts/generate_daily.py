from __future__ import annotations

import base64
import html
import io
import json
import os
import re
import shutil
import urllib.request
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from openai import OpenAI
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT / "images"
METADATA_DIR = ROOT / "metadata"
JST = ZoneInfo("Asia/Tokyo")
TARGET_SIZE = (1080, 480)


def env(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(env(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


RESEARCH_MODEL = env("RESEARCH_MODEL", "gpt-5-mini")
RESEARCH_MAX_OUTPUT_TOKENS = env_int(
    "RESEARCH_MAX_OUTPUT_TOKENS", 450, 250, 1200
)
SEARCH_CONTEXT_SIZE = env("SEARCH_CONTEXT_SIZE", "low")
IMAGE_MODEL = env("IMAGE_MODEL", "gpt-image-2.5-sunburst")
IMAGE_QUALITY = env("IMAGE_QUALITY", "medium")
KEEP_DAYS = env_int("KEEP_DAYS", 30, 1, 365)
FORCE = env("FORCE", "false").lower() in {"1", "true", "yes"}


def extract_json(text: str) -> dict[str, str]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("調査結果からJSONを読み取れませんでした")
    data = json.loads(cleaned[start : end + 1])
    required = ("title", "summary", "source_url", "image_subject")
    for key in required:
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"調査結果の {key} が空です")
        data[key] = data[key].strip()
    parsed = urlparse(data["source_url"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source_urlが有効なURLではありません")
    return data


def research_anniversary(client: OpenAI, now: datetime) -> dict[str, str]:
    date_text = now.strftime("%Y年%m月%d日")
    prompt = f"""
今日は日本時間の{date_text}です。日本語のWebを検索して、この日の「今日は何の日」に該当する記念日・文化・科学・季節の話題を確認してください。
信頼できる公的機関、団体、博物館、報道機関などを優先し、由来を確認できるものから、画像にしやすく明るい題材を1件だけ選んでください。災害、事故、戦争、人物の死去を中心にした題材は避けてください。
出力は次のキーだけを持つ短いJSONオブジェクトにしてください。Markdownや説明文は不要です。
{{"title":"画像内にそのまま載せる短い日本語名","summary":"由来を80文字以内で要約","source_url":"根拠としたページのURL","image_subject":"画像に描く具体的な題材を日本語で簡潔に"}}
""".strip()
    response = client.responses.create(
        model=RESEARCH_MODEL,
        tools=[
            {
                "type": "web_search",
                "search_context_size": SEARCH_CONTEXT_SIZE,
            }
        ],
        input=prompt,
        max_output_tokens=RESEARCH_MAX_OUTPUT_TOKENS,
    )
    return extract_json(response.output_text)


def make_image_prompt(item: dict[str, str], now: datetime) -> str:
    season = f"{now.month}月の日本の季節感"
    return f"""
最終表示サイズは横1080px×縦480px（アスペクト比9:4）の横長画像。
テーマは「{item['title']}」。描く題材は「{item['image_subject']}」。{season}を背景や色調に自然に取り入れる。
80年代のアートスタイルを意識したアニメイラスト風。
現代的なアニメ調の美しい女性キャラクターを主役にし、テーマにふさわしい情景を組み合わせる。
フラットでシンプル、要素を絞った引き算のデザイン。大胆で読みやすい構図。横長への切り抜きを考慮し、重要な人物・題材・文字は中央の安全領域に収める。
画像内に表示する文字は、正確な日本語の「{item['title']}」だけ。日付、説明文、ロゴ、透かし、署名、そのほかの文字は一切入れない。
""".strip()


def image_bytes(result: object) -> bytes:
    first = result.data[0]
    encoded = getattr(first, "b64_json", None)
    if encoded:
        return base64.b64decode(encoded)
    url = getattr(first, "url", None)
    if url:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    raise ValueError("画像APIの応答に画像データがありません")


def generate_image(client: OpenAI, prompt: str, destination: Path) -> None:
    result = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size="1536x1024",
        quality=IMAGE_QUALITY,
    )
    raw = image_bytes(result)
    with Image.open(io.BytesIO(raw)) as source:
        normalized = ImageOps.exif_transpose(source).convert("RGB")
        final = ImageOps.fit(
            normalized,
            TARGET_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        final.save(destination, "JPEG", quality=93, optimize=True, progressive=True)


def remove_expired(today_text: str) -> None:
    cutoff = datetime.strptime(today_text, "%Y-%m-%d").date() - timedelta(
        days=KEEP_DAYS - 1
    )
    for directory, suffix in ((IMAGES_DIR, ".jpg"), (METADATA_DIR, ".json")):
        for path in directory.glob(f"????-??-??{suffix}"):
            try:
                file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_date < cutoff:
                path.unlink()


def load_entries() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in METADATA_DIR.glob("????-??-??.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if all(data.get(key) for key in ("date", "title", "image_url")):
                entries.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(entries, key=lambda item: item["date"], reverse=True)


def build_feed(entries: list[dict[str, str]], now: datetime) -> None:
    repo = env("GITHUB_REPOSITORY", "rockwari/daily-image-feed")
    channel_link = f"https://github.com/{repo}"
    items: list[str] = []
    for entry in entries:
        generated_at = datetime.fromisoformat(entry["generated_at"])
        source_url = html.escape(entry["source_url"], quote=True)
        image_url = html.escape(entry["image_url"], quote=True)
        title = html.escape(entry["title"])
        summary = html.escape(entry["summary"])
        image_size = ROOT.joinpath(entry["image_path"]).stat().st_size
        items.append(
            f"""    <item>
      <title>{title}</title>
      <link>{source_url}</link>
      <guid isPermaLink="false">daily-image-{entry['date']}</guid>
      <pubDate>{format_datetime(generated_at)}</pubDate>
      <description>&lt;img src=&quot;{image_url}&quot; alt=&quot;{title}&quot; /&gt;&lt;p&gt;{summary}&lt;/p&gt;</description>
      <enclosure url="{image_url}" length="{image_size}" type="image/jpeg" />
      <media:content url="{image_url}" type="image/jpeg" medium="image" width="1080" height="480" />
    </item>"""
        )
    item_block = "\n".join(items)
    feed = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>今日は何の日 - Daily Image Feed</title>
    <link>{channel_link}</link>
    <description>毎日1枚、「今日は何の日」を題材にした画像を配信します。</description>
    <language>ja</language>
    <lastBuildDate>{format_datetime(now)}</lastBuildDate>
{item_block}
  </channel>
</rss>
"""
    (ROOT / "feed.xml").write_text(feed, encoding="utf-8")


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEYが設定されていません")

    now = datetime.now(JST)
    date_text = now.strftime("%Y-%m-%d")
    IMAGES_DIR.mkdir(exist_ok=True)
    METADATA_DIR.mkdir(exist_ok=True)
    image_path = IMAGES_DIR / f"{date_text}.jpg"
    metadata_path = METADATA_DIR / f"{date_text}.json"

    if image_path.exists() and metadata_path.exists() and not FORCE:
        print(f"{date_text} は生成済みです。APIは呼び出しません。")
        return

    client = OpenAI()
    item = research_anniversary(client, now)
    prompt = make_image_prompt(item, now)

    temporary_image = IMAGES_DIR / f".{date_text}.tmp.jpg"
    try:
        generate_image(client, prompt, temporary_image)
        temporary_image.replace(image_path)
    finally:
        temporary_image.unlink(missing_ok=True)

    repo = env("GITHUB_REPOSITORY", "rockwari/daily-image-feed")
    branch = env("GITHUB_REF_NAME", "main")
    image_relative = image_path.relative_to(ROOT).as_posix()
    image_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{image_relative}"
    metadata = {
        "date": date_text,
        "title": item["title"],
        "summary": item["summary"],
        "source_url": item["source_url"],
        "image_subject": item["image_subject"],
        "image_path": image_relative,
        "image_url": image_url,
        "generated_at": now.isoformat(),
        "research_model": RESEARCH_MODEL,
        "image_model": IMAGE_MODEL,
        "image_quality": IMAGE_QUALITY,
        "final_size": "1080x480",
        "prompt": prompt,
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    remove_expired(date_text)
    entries = load_entries()
    build_feed(entries, now)
    shutil.copyfile(metadata_path, ROOT / "latest.json")
    print(f"生成完了: {item['title']} -> {image_relative}")


if __name__ == "__main__":
    main()

