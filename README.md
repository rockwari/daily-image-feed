# Daily Image Feed

毎日、「今日は何の日」を日本語のWeb情報から1件選び、その題材に沿った画像をOpenAI APIで生成してRSS配信するためのリポジトリです。

## 動作

- 毎朝 5:15（日本時間）にGitHub Actionsを実行
- Web検索は1日1回だけ行い、視覚化しやすい題材を1件選定
- 画像生成プロンプトに最終表示サイズ `1080×480` と指定
- APIでは横長画像を生成後、中央基準で正確に `1080×480` へ切り抜き・縮小
- 画像、選定根拠、RSSをリポジトリへ自動コミット
- 同じ日本日付の画像が存在する場合は再生成しない
- 直近30日分を保持

RSS URL:

```text
https://raw.githubusercontent.com/rockwari/daily-image-feed/main/feed.xml
```

## 必須設定

GitHubの `Settings` → `Secrets and variables` → `Actions` → `Secrets` に、次を登録します。

```text
OPENAI_API_KEY
```

## 消費量を抑える設定

初期値は調査1回・短い応答・中品質画像です。必要な場合だけ、ActionsのRepository Variablesで変更できます。

| Variable | 初期値 | 用途 |
|---|---|---|
| `RESEARCH_MODEL` | `gpt-5-mini` | 「今日は何の日」の調査モデル |
| `RESEARCH_MAX_OUTPUT_TOKENS` | `450` | 調査結果の最大出力トークン数 |
| `SEARCH_CONTEXT_SIZE` | `low` | Web検索のコンテキスト量 |
| `IMAGE_MODEL` | `gpt-image-2.5-sunburst` | 画像生成モデル |
| `IMAGE_QUALITY` | `medium` | `medium` または `high` など |
| `KEEP_DAYS` | `30` | 保存日数 |

`Actions` → `Generate daily image` → `Run workflow` から手動実行できます。通常は同日分を再利用し、`force` を有効にした場合だけ同日の画像を作り直します。

