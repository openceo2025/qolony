# QOLONY

**アリのコロニーに学ぶ、集合知のプロジェクト管理**

[English](README.md)

QOLONY の根っこにある考え:

> 人は管理される資源ではない。
> チームは、共通のルールと互いの見える化と信頼によって自己組織化する、生きたシステムである。

アリのコロニーのように、一人ひとりは自分の持ち場で自律的に動き、それでも群れは一つの生き物のように動く。
よりよい道を見つけ、絶えず適応し、自然に仕事を分け合い、一緒に成果を届ける。

## できること

LAN 内のチームで使う、タスク・工数・進捗・評価の管理ツールです。
1タスクの担当者は1人。複数人で行う仕事はタスクを分割して表します。

| 画面 | 役割 |
|---|---|
| WBS / Gantt | タスクの木構造、日程、依存関係 |
| 巣の外 | タスクの木をお菓子として描く。着手中はアリが食べ、完了したかけらは巣へ運ばれる |
| My task / Today | 自分のタスクと、実績工数を記録する Start / Pause / Done のタイマー |
| Split | 表計算のような表でタスクを子に分割（コピー・貼り付け・下/右へ埋める） |
| Progress | 作業速度と進捗率のグラフ、Blue / Yellow / Red の信号 |
| Members / Ranking | 休暇・コミット率・役割と、貢献（成果量・速度・教育・指導時間）の見える化 |

## はじめかた

必要なもの: Python 3.12、Node.js 20 以上。

```bash
cd backend
python -m pip install -r requirements.txt
cd ..
./scripts/run_backend.sh            # macOS / Linux
pwsh -File scripts/run_backend.ps1  # Windows
```

`http://localhost:8000/` を開きます。空のDBで初めて起動すると、`backend/config/settings.yaml` の
架空のメンバー全員ぶんのデモタスクが作られます。`users.members` を自分のチームに書き換えてください。
管理タブに入るときは Top Manager のパスワード（既定は `change-me`）を聞かれます。必ず変更してください。

### 開発

```bash
cd backend && python -m pytest tests -q
cd frontend && npm install && npm run check && npm test
```

`frontend/field-lab.html`（開発サーバーのみ）で、架空データの「巣の外」を確認できます。

## 任意: iQUAVIS 連携

PLM の iQUAVIS と、Excel マクロブックを中継して双方向に同期できます。**既定では無効**で、
無効のままでも他の機能はすべて使えます。マクロブックとメーカー資料はこのリポジトリに含みません。

## セキュリティ

信頼できる LAN 内での利用を前提にしています。パスワードは管理タブに入るための簡易な確認で、
API を守る認証ではありません。インターネットへ直接公開しないでください。

## ライセンス

[MIT](LICENSE)
