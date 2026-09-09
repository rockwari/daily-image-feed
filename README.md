# Daily Image Feed

毎日、Yahoo!きっず「今日は何の日」の当日情報を参照し、その題材に沿った画像をOpenAI APIで生成してRSS配信するためのリポジトリです。

## 動作

- 毎朝 5:15（日本時間）にGitHub Actionsを実行
- Yahoo!きっずの構造化データから当日の記念日名と説明を取得
- 検索用のAI・Web検索APIは使用せず、OpenAI APIは画像生成だけに使用
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

初期値は画像1枚・中品質です。必要な場合だけ、ActionsのRepository Variablesで変更できます。

| Variable | 初期値 | 用途 |
|---|---|---|
| `IMAGE_MODEL` | `gpt-image-2.5-flare` | 画像生成モデル（現在はワークフローでFlareに固定） |
| `IMAGE_QUALITY` | `medium` | `medium` または `high` など |
| `KEEP_DAYS` | `30` | 保存日数 |

`Actions` → `Generate daily image` → `Run workflow` から手動実行できます。通常は同日分を再利用し、`force` を有効にした場合だけ同日の画像を作り直します。
