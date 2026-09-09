from __future__ import annotations

import base64
import html
import io
import json
import os
import shutil
import urllib.request
from datetime import datetime, timedelta
from email.utils import format_datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT / "images"
METADATA_DIR = ROOT / "metadata"
JST = ZoneInfo("Asia/Tokyo")
TARGET_SIZE = (1080, 480)
TODAY_SOURCE_URL = "https://kids.yahoo.co.jp/today"
MAX_SOURCE_BYTES = 2_000_000


def env(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(env(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


IMAGE_MODEL = env("IMAGE_MODEL", "gpt-image-2.5-flare")
IMAGE_QUALITY = env("IMAGE_QUALITY", "medium")
KEEP_DAYS = env_int("KEEP_DAYS", 30, 1, 365)
FORCE = env("FORCE", "false").lower() in {"1", "true", "yes"}


class NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._inside_next_data = False
        self._chunks: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("id") == "__NEXT_DATA__":
            self._inside_next_data = True

    def handle_data(self, data: str) -> None:
        if self._inside_next_data:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._inside_next_data:
            self._inside_next_data = False

    @property
    def next_data(self) -> str:
        return "".join(self._chunks).strip()


def fetch_anniversary(now: datetime) -> dict[str, str]:
    request = urllib.request.Request(
        TODAY_SOURCE_URL,
        headers={
            "User-Agent": (
                "daily-image-feed/1.0 "
                "(+https://github.com/rockwari/daily-image-feed)"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ja-JP,ja;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(MAX_SOURCE_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except OSError as exc:
        raise RuntimeError(f"Yahoo!きっずの取得に失敗しました: {exc}") from exc

    if len(raw) > MAX_SOURCE_BYTES:
        raise RuntimeError("Yahoo!きっずの応答サイズが上限を超えました")

    parser = NextDataParser()
    parser.feed(raw.decode(charset, errors="strict"))
    if not parser.next_data:
        raise RuntimeError("Yahoo!きっずの構造化データが見つかりません")

    try:
        page_data = json.loads(parser.next_data)
        results = page_data["props"]["pageProps"]["todayResponse"]["results"]
        source_date = str(results["date"])
        memory = results["memories"][0]
        title = memory["title"].strip()
        description = memory["description"].strip()
    except (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Yahoo!きっずの記念日情報を解析できませんでした") from exc

    expected_date = now.strftime("%m%d")
    if source_date != expected_date:
        raise RuntimeError(
            "Yahoo!きっずの日付が日本時間の当日と一致しません: "
            f"expected={expected_date}, actual={source_date}"
        )
    if not title or not description:
        raise RuntimeError("Yahoo!きっずの記念日名または説明が空です")

    return {
        "title": title,
        "summary": description,
        "source_url": TODAY_SOURCE_URL,
        "image_subject": f"{title}を象徴する情景。由来は「{description}」",
    }


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

    item = fetch_anniversary(now)
    prompt = make_image_prompt(item, now)
    client = OpenAI()

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
        "topic_source": TODAY_SOURCE_URL,
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
