# 保存ルートの原rlog復旧と欠損解消後の再監査

2026-11-05。ユーザーがConnectで保存した5ルートについて、既存認証でファイル一覧を照会し、原rlogだけを取得した。車両への通信・アップロード要求・保存設定変更・映像取得は行っていない。

## 1. 原ログを復旧できた

|ルート末尾|取得・読み取りできたrlog区間|
|---|---:|
|59|0～14、15区間|
|5a|0～14、15区間|
|5c|0～33、34区間|
|68|0、1区間|
|6c|0、1区間|

合計66ファイル、728,849,963 bytes。ダウンロード失敗0。全て圧縮解除してCap'n Protoイベントを読み取り、取得ファイルと解析元のbyte数・SHA256が66件全て一致した。ConnectのPreservedフラグ自体は照会していない。

原rlogのローカル保存先:

```text
/Users/seisyo58/Documents/Codex/2026-11-03/cabana-openpilot-acc/work/raw_archive_20261105/
```

再抽出NPZとメタデータ:

```text
/Users/seisyo58/Documents/Codex/2026-11-03/cabana-openpilot-acc/work/reextracted_20261105/
```

生ログ・個人の走行データはこのGitブランチへ追加しない。[監査JSON](../evidence/leaf_raw_recovery_20261105.json)には原ファイルのハッシュ・集計・抽出した限定イベントだけを保存し、認証情報・署名URL・位置・映像は含めない。

59では旧NPZと比較した41配列、5cでは27配列（共通の選択CAN、states、t0）がすべて完全一致した。5aは以前11区間しかなかったため、23配列が増えた/変わった。新しい解析では復旧後の全15区間を優先する。

## 2. 5aの解除未観測を解消

[以前の観測分類](leaf_event_classification_20261105.md)で右打ち切りだった282.952秒開始の保持候補は、**308.782秒にOFFになるまで追跡できた**。

- OFF直前の受信RESは7フレーム、最初は約79.8ms前。
- 送信要求RESは10フレーム、最初は約76.5ms前。近傍の受信/送信要求CANCELは0。
- carControlの近傍cancel/resumeはともに0。送信RESの生成経路はcarControl.resumeだけでは説明できず、ICBMによるパススルー等と区別する必要がある。
- 2B0有効化は約10.3ms前、1CB候補OFFは約40.4ms後、1C3 bit2 OFFは約60.2ms後。

この追加で、保持候補13区間すべてのOFFを観測した。文脈は受信RESあり10例・装置CANCEL要求あり3例へ更新する。中央部が取れる12区間の共通シグネチャは変わらず、評価サンプルは17,996から18,766へ増えた。短い1区間は中央部評価対象外のまま。

5aの1C3受信数は63,322→87,320、carStateは63,003→87,003。通常要求候補は30,123→44,991、停止保持候補は2,755→3,521。走行中の協調制動候補は674点のままで、復旧した区間に同じ定義の追加制動はなかった。境界比較の45入口・38出口という主集計も変わらない。

## 3. ソフト介在をcarControlまで再確認

原ログから全carControlを追加抽出した。3例の装置CANCEL要求に先行するcarControl.cancelを確認できた。OFF時刻に対する最初のcancelの受信記録は以下。

|走行 / OFF秒|ペダル入力|carControl.cancel|sendcan CANCEL|
|---|---|---:|---:|
|59 / 464.756|gasPressed立ち上がり 約77.7ms前|約71.2ms前|約56.4ms前|
|5c / 843.124|brakePressed立ち上がり 約106.8ms前|約101.8ms前|約86.1ms前|
|5c / 1234.176|brakePressed立ち上がり 約88.2ms前|約83.5ms前|約76.0ms前|

ペダル入力→carControl.cancel→送信要求という記録上のつながりを確認した。純正だけのペダル解除として扱わない。記録順序はECUの受理や実際の制動解除の証明ではない。

## 4. 実測した比較条件と未取得の条件

|条件|59|5a|5c|
|---|---|---|---|
|記録ソフト|leaf-planner-icbm / c214728b24|leaf-planner-icbm / c214728b24|leaf-custom / 972c5f6d3c|
|initDataのdirty|false|false|false|
|openpilotLongitudinalControl|false|false|false|
|pcmCruise|true|true|true|
|走行ACC中のGEARBOX raw|4のみ（DBC上D）|4のみ（DBC上D）|4のみ（DBC上D）|
|走行ACC中の2F4 / 5B3設定候補|両方1のみ|両方1のみ|両方0のみ|

ギアは記録版のパーサーとDBCのGEARBOX定義を照合した。走行ACC条件では3ルートともDであり、観測された通常要求境界値の違いをD/B混在で説明する根拠はない。ただし速度等を一致させた比較ではない。

5aは開始から約47秒までe-Pedal設定候補0、その後1。59にも起動時や末尾に候補0がある。従って「ONルート」という申告を全フレームの設定状態へ一般化せず、走行ACCに絞った実受信値で確認した。設定候補は作動量・実回生・保持の成立ではない。

SOC・ECO・電池温度・充電/回生上限は今回も確定できていない。carState.fuelGaugeやregenBrakingの0/falseは、記録版Nissanパーサーが設定していない既定値であり、SOC 0%や回生なしの測定値として使えない。原CAN全件の件数でも1D4/1DAは3走行とも0だった。

またON側とOFF側はソフト版も違う。e-Pedalだけを変えた対照実験ではなく、ボタン介在・速度・走行環境等の交絡を残す。

## 5. Pandaの記録状態

原rlogのpandaStatesは、起動診断中elm327、その後nissanへ切り替わる。nissanを最初に記録した時刻は59が9.307秒、5aが8.582秒、5cが7.213秒。1D6のbus2記録が終わる約9.198 / 8.473 / 7.105秒と近接しており、[起動直後だけ両側へ見える構成](leaf_transport_paths_20261105.md)と整合する。

harnessStatusはflipped。これを物理配線図の端点やbus番号へ直接置き換えず、Pandaの0/2 lookup交換と区別する。リレー接点状態を直接記録した値ではない。initDataのソフト版もPandaフラッシュ済みバイナリの一致確認とは異なる。

## 再現用ツール

- [fetch_saved_logs.py](../fetch_saved_logs.py): 明示したルートのlogs一覧だけを取得し、圧縮原rlogとハッシュ付きmanifestを保存。署名URL・トークンを出力しない。アップロード要求・保存操作・共有操作は行わない。
- [extract_saved_logs.py](../extract_saved_logs.py): 保存済みrlogを読み、選択CAN・carState・carControl・限定メタデータを再抽出。GPS・映像を抽出しない。
- [reaudit_saved_routes.py](../reaudit_saved_routes.py): 取得元ハッシュと解析元の一致、旧NPZとの比較、設定/ギア、最新の観測分類を集計。

既存のopenpilot認証とPython環境で実行する。抽出・再監査の出力は既存ファイルを上書きしない。取得ツールは既存の原ファイルを再利用する。

```sh
PYTHONPATH=opendbc_repo .venv/bin/python -m openpilot.tools.leaf_research.fetch_saved_logs \
  --device 0abcc72a352961a0 --out-dir /path/to/raw_archive \
  00000059--02589b1b20 0000005a--34bc682d3e 0000005c--6a84234ad4 \
  00000068--32fe1f4a1e 0000006c--4fcd507cdc

PYTHONPATH=opendbc_repo .venv/bin/python -m openpilot.tools.leaf_research.extract_saved_logs \
  --out-dir /path/to/extracted /path/to/raw_archive/0000005a--34bc682d3e

.venv/bin/python -m openpilot.tools.leaf_research.reaudit_saved_routes \
  --extracted-dir /path/to/extracted --legacy-dir /path/to/old/work \
  --manifest /path/to/raw_archive/manifest.json --out /tmp/raw-reaudit.json
```

再監査は上記3走行に加え、診断2ルートも抽出したディレクトリを使用する。機密ログはGitへ追加せず、所在・ハッシュと限定した結果だけを保存する。
