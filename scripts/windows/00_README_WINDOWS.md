# QOLONY Windows 版の入れ方・更新のしかた

この ZIP だけで動きます。Python や Node.js を入れる必要はありません（`runtime\` に同梱）。
データベースと設定は **データフォルダー（既定 `C:\QOLONY_DATA`）** に置き、アプリのフォルダー（既定 `C:\qolony`）とは分けます。
アプリを入れ替えても、データと設定は上書きされません。

## 1. ZIP を確かめて展開する

GitHub の Releases から `qolony_windows_*.zip` と、同じ名前の `.sha256.txt` をダウンロードします。
USB で別の PC へ運ぶときも、2つを同じフォルダーに置きます。

```powershell
$zip = 'D:\QOLONY_UPDATE\qolony_windows_YYYYMMDD.zip'
$expected = ((Get-Content -LiteralPath "$zip.sha256.txt" -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "ZIP が壊れています。もう一度ダウンロードしてください。" }
Expand-Archive -LiteralPath $zip -DestinationPath 'D:\QOLONY_UPDATE\bundle'
```

展開先は、今の `C:\qolony` とは別の、**浅いフォルダー**（例: `D:\QOLONY_UPDATE\bundle`）にします。
ダウンロードフォルダーの奥など深い場所に展開すると、Windows のパスの長さの上限（260文字）を超えて失敗することがあります。

## 2. 入れる・更新する

QOLONY を動かしている黒いウィンドウがあれば `Ctrl+C` で止めます。iQUAVIS 連携で Excel を使っている場合は、Excel もすべて閉じます。

更新の前に、データフォルダーを丸ごと控えておきます。

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
Copy-Item -LiteralPath 'C:\QOLONY_DATA' -Destination "C:\QOLONY_DATA_backup_$stamp" -Recurse
```

そのあと更新スクリプトを実行します。

```powershell
PowerShell -ExecutionPolicy Bypass -File 'D:\QOLONY_UPDATE\bundle\update_qolony_app.ps1'
```

スクリプトがすること:

- ZIP の中身が壊れていないか（`SHA256SUMS.txt`）を確かめる
- 今のアプリを `C:\qolony.backup_日時` に残し、新しいアプリを `C:\qolony` に置く
- **今までのアプリに入っていた設定を、データフォルダーへ移す**（データフォルダーにまだ無いときだけ）
  - `C:\qolony\backend\config\settings.yaml` → `C:\QOLONY_DATA\settings.yaml`（メンバー・パスワード・連携設定）
  - `C:\qolony\start_qolony.bat` の `set "QOLONY_..."` の行 → `C:\QOLONY_DATA\qolony_env.bat`（DB の場所・連携ブックの場所・同期時刻など）
- `C:\qolony\backend\data\iquavis_link_targets.xlsx` があれば、新しいアプリへそのまま引き継ぐ
- データベースには触れない

引き継いだときは `Kept your settings` / `Kept your start options` / `Previous app` の行が出ます。
これらが出ないのは、`C:\qolony` に前の版がなかったときです。今までの DB を使うなら、起動する前に
下の「起動の設定」で `QOLONY_DB_PATH` を書き、設定ファイルを `C:\QOLONY_DATA\settings.yaml` に置きます。

## 3. 起動する

`C:\qolony\start_qolony.bat` をダブルクリックします。黒いウィンドウが開きます（使っている間は閉じません）。

- 2行目の `data:` が使いたいデータベースになっているか確かめます。違っていたら `Ctrl+C` で止めて、「起動の設定」を直します。
- `startup: QOLONY is ready` と `Application startup complete.` が出たら起動完了です。ブラウザーで `http://localhost:8000/` を開きます。
- 最初の起動で、データベースの形が新しい版に合わせて自動で更新されます（今までのデータは消えません）。
- 新しく入れた場合は `C:\QOLONY_DATA\qolony.db` が作られます。メンバーは設定ファイルの `users.members` から作られます。
  自分たちの設定にするときは、`C:\qolony\backend\config\settings.yaml` を `C:\QOLONY_DATA\settings.yaml` へ写して書き換えます。

## 4. 元に戻す

1. QOLONY を止めます。
2. `C:\qolony` を別名にし、`C:\qolony.backup_日時` を `C:\qolony` に戻します。
3. データベースも戻すときは、手順2で控えた `C:\QOLONY_DATA_backup_日時` から戻します。
   新しい版で一度起動したデータベースは形が新しくなっているので、古い版に戻すときはデータベースも控えから戻してください。

## 起動の設定（`C:\QOLONY_DATA\qolony_env.bat`）

必要な行だけ書きます。例:

```bat
set "QOLONY_DB_PATH=C:\QOLONY_DATA\qolony.db"
set "QOLONY_PORT=8000"
set "QOLONY_IQUAVIS_EDITOR_PATH=C:\QOLONY_DATA\editor.xlsm"
set "QOLONY_IQUAVIS_SYNC_TIME=02:00"
```
