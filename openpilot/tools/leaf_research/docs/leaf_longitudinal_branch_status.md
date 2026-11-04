# Leaf縦制御・検証ブランチの初期実装

- ブランチ：`experiment/leaf-longitudinal`
- 基点：`leaf-custom` の `7b28fbe1eb`（e-Pedal状態DBCのPR #6を含む）
- コミット：`7c6d693877` — Leaf: add receive-only longitudinal experiment recorder
- 独立した作業フォルダ：`/Users/seisyo58/Documents/Codex/2026-11-03/cabana-openpilot-acc/work/leaf-longitudinal`
- 元の開発フォルダはleaf-customのまま。両方の作業ツリーに未コミットの変更なし。

## 今回追加したもの

`openpilot/tools/leaf_longitudinal/monitor.py` は、キーボード・本体接続ゲームパッドの入力と、車両状態・CAN・openpilotの要求を同じJSONLファイルへ記録する独立ツール。保存済みデータの再生にも対応。

メッセージの配信、CAN送信、Paramsの変更、Joystick Debug Modeの有効化は行わない。通常のopenpilotは独立して従来の動作を続ける。ゲームパッドをPCにつないでネットワーク転送する機能は未実装。PCからのキーボード操作はSSH端末で利用する構成。

加速度表示は入力軸1に対し1 m/s²という仮の比較スケールで、実車用の駆動・制動量に換算しない。入力途絶、ペダル介入、停車、車両情報の鮮度異常などを判定する。比較可能という判定が出ても、送信許可は常にfalse。

## 検証

8件のテスト成功。入力途絶、介入、停止、範囲外入力、設定状態と制動モードの区別、模擬購読による記録、再生、上書き拒否などを確認した。Ruffのチェックとgit diff --checkも成功。

comma実機での購読動作、USBゲームパッドの認識・対応する軸・ボタン、実車からのデータ取得は未確認。ブランチはローカルのみ。push、PR作成、commaへの導入は未実施。

## 次に確認できるもの

commaへ導入した後、まず入力なしの受信記録で車両状態の取得と保存を確認する。コマンドはリポジトリのルートから、通常のPython環境で実行する。

```sh
python openpilot/tools/leaf_longitudinal/monitor.py record --duration 60 --out /data/leaf-monitor-001.jsonl
```

キーボードは対話可能なSSH端末から：

```sh
python openpilot/tools/leaf_longitudinal/monitor.py record --input keyboard --duration 60 --out /data/leaf-keyboard-001.jsonl
```

W/Sで比較用の正負入力、空白で解除、Qで終了。入力は認識したキーから0.2秒で失効し、実車は操作しない。既存のjoystick_control.pyはDebug Modeを有効化するので、この記録手順には使わない。

CANは約20Hzの最新バッチの観測であり、全フレームを残す記録ではない。タイミング検証には通常の生rlogを併用する。sendcanの観測も、車両での指令受理の証明ではない。
