# AGENTS.md

AI コーディングエージェントと開発者向けのプロジェクト運用ルールです。
現行の正はソースコード、設定ファイル、テストです。この文書が実装と食い違う場合は、実装を確認してから文書を更新してください。

## 1. Output Contract

変更後は必ず以下を短く報告する。

1. 変更点
2. 起動手順
3. テスト手順と結果
4. 既知の制限

## 2. Best Practices

- 可読性、保守性、テスト容易性、拡張性を優先する。
- 既存機能を壊さない。変更時は関連テストを実行する。
- コード内コメントは日本語で記述する。仕様意図、落とし穴、理由の説明に限定する。
- UIからDBへ直接アクセスしない。すべてAPI経由とする。
- ドメイン計算は純粋関数に寄せ、ユニットテストで担保する。
- DBアクセスはRepository層に閉じ込める。
- DBスキーマ変更はAlembicマイグレーションで行い、既存データを消さない。
- ソースの読み書きは UTF-8。`.ps1` は ASCII のみで書く（Windows PowerShell 5.1 は BOM 無しを ANSI として読む）。

## 3. Architecture

- Backend: `backend/`
  - FastAPI entrypoint: `backend/app/main.py`
  - Runtime wiring: `backend/app/runtime.py`
  - Service layer: `backend/app/tms_core/services/core.py`
  - Repository/models: `backend/app/tms_core/db/`
  - Domain logic: `backend/app/tms_core/domain/`
  - Optional iQUAVIS integration: `backend/app/iquavis/`
- Frontend: `frontend/`
  - App/tabs/query wiring: `frontend/src/App.tsx`
  - Views: `frontend/src/views/`
  - API client: `frontend/src/api/client.ts`
  - View-model helpers: `frontend/src/lib/`
- Launchers: `scripts/run_backend.ps1`, `scripts/run_backend.sh`
- Settings: `backend/config/settings.yaml`（DB は既定で `backend/data/qolony.db`、`QOLONY_DB_PATH` で上書き）

## 4. Product Rules Implemented In Source

### 4.1 Task / WBS

- 1タスクの担当者は1人。複数人作業はタスク分割で表現する。
- タスクは親子階層を持つ。工数は分単位で保持する。
- 30分単位の丸めは `backend/app/tms_core/domain/rounding.py` を正とする。
- 親子進捗集計は `backend/app/tms_core/domain/progress.py` を正とする。

### 4.2 Timer / Actual Manhour

- 実績は Today の Start / Pause / Done と画面離脱時の確定送信で記録する。
- 1ユーザーの同時稼働タイマは常に1つ。
- OJTはTeacher指定を持ち、TeachingTime評価に使う。

### 4.3 Members / Daily Actuals

- Members View は休暇、コミット率、役割、調子、称号、特殊能力タグを扱う。
- 休暇日とコミット率上書きは日次確定計算に反映する。

### 4.4 Progress / Signal

- 速度、臨界速度、安全速度、Blue/Yellow/Red判定は `backend/app/tms_core/domain/speed.py` を正とする。
- タスク信号は `backend/app/tms_core/domain/signal.py` を正とする。

### 4.5 Ranking / Feedback

- Ranking は OutputVolume、WorkSpeed、EducationEfficiency、TeachingTime を扱う。
- 公開範囲ルールは `backend/app/tms_core/domain/ranking.py` を正とする。
- 下位者を推定できるUIや文言を追加しない。

### 4.6 Colony view（巣の外）

- タスクツリーをお菓子にまとめる（`frontend/src/colony/liveModel.ts`）。角砂糖 = 1階層、包みキャンディー = 2階層、
  板チョコ = 3階層まで。割れ方は階層と個数が大まかに合う程度にとどめ、仕事の中身はズーム時の吹き出しだけで見せる。
- 着手中 = アリが食べている、完了 = かけらが巣へ運ばれて欠ける。

### 4.7 Optional iQUAVIS sync

- 既定は無効（`integrations.iquavis.enabled: false`）。無効でも他の機能はすべて動くこと。
- 同期・競合・強制取込の規則は `backend/app/iquavis/` を正とする。

## 5. Error Handling

- 例外は握りつぶさない。
- ユーザー起因エラーはUIに具体的に案内する。
- システムエラーはログにスタックトレースを残し、UIには安全なメッセージを出す。
- タイマ周辺は二重起動、終了漏れ、通信断で破綻しないように扱う。
- 同時編集は先着優先。後着には誰が何に変えたかを添えて 409 を返し、自動マージしない。

## 6. Quality Gate

```bash
cd backend && python -m pytest tests -q
cd frontend && npm run check && npm test
```

`tests` の明示は必須（`backend/` 直下の pytest 一時ディレクトリを拾うと収集が失敗するため）。

## 7. Git / Generated Files

- DB、ログ、`node_modules`、`frontend/dist`、配布zip、pytest一時ディレクトリを管理対象にしない。
- 既存データDBを削除・初期化しない。
