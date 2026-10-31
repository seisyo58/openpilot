# リーフIC: ProPILOT Parking中の制御抑止（実験版）

`leaf-custom`向けの個人用変更。対象は `NISSAN_LEAF_IC` のみ。

2026-10-29に `sunnypilot/sunnypilot:master` (`6f7a2c94c1`) へリベース。
opendbcは `aa9bff6f5442cef8cb8e25e143ef38865c276ce8`、pandaは
`8a16e089b88593914823743df29e1c30b7dd3e00` のソースにPPP抑止差分を適用した。
opendbcには [Nissan ICBM対応 #342](https://github.com/sunnypilot/opendbc/pull/342) が含まれる。
両ディレクトリは通常ファイルとして保持しているため、今後も上流のサブモジュール更新を別途取り込む必要がある。
Bus 2 / `0x169` (LKAS) / byte 6 mask `0x20` を `PPP_ACTIVE_CANDIDATE` として読む。
この意味は実ログからの推定で、通常のProPILOTとの区別・実車動作はまだ検証していない。

## 動作

- 有効なPPPフレームを受信すると、OpenpilotとMADSをともにdisabledにし、再エンゲージを抑止する。
- CarControllerからの操舵・クルーズボタン（キャンセルを含む）・HUD送信を止める。
- Pandaも独立に判定し、ホスト送信を拒否する。PPPの有効フレームだけBus 2→0に転送する。抑止中の純正HUDとドライバーのボタンは転送する。
- PPPビットが0に戻っても抑止を維持する。正常な非PPP信号があり、クルーズが解除されている状態で、クルーズメインをOFF→ONすると再エンゲージ可能になる。SET/RESやブレーキ解除だけでは解除しない。
- 起動直後は信号を確認するまで抑止する。長さ・CRC・カウンター異常、100ms超の受信途絶では抑止を維持し、正常通信の復帰だけでは解除しない。カウンターは連続値、または1〜2フレームの欠落を許容する。
- `carStateSP`の欠落・異常でもSelfdriveD側を抑止する。このサービスの追加監視は対象車種だけ。
- 永続設定のMADSトグル自体は変更しない。実行状態をdisabledにし、解除時は1度だけ解除音を出す。

## CAN転送の処理順

FDCANは受信処理より先に転送を判断する。アドレスだけの転送判定に加え、実フレームの長さ・CRC・カウンター・PPPビットを検証する入口を追加した。
最初のPPPフレームは状態確定に使い、次の有効フレームから転送する（100Hzで通常約10ms後）。終了フレームを直前のPPP状態で誤転送しない。これは自動駐車の正常動作を実車で保証するものではない。

## 配布ブランチと生成物

このブランチはフルソース構成。Cap'n Protoの生成ソースとPandaファームウェアは、更新したソースからビルドする。
ファームウェアを旧版のまま使うと純正PPPの転送ができないため、Pythonだけの部分適用はしない。
今回のリベースでは実機へのインストール・フラッシュは行っていない。

ファームウェア再生成（Python依存とArm GNU Toolchain 13.2.Rel1が必要）:

```sh
cd panda
PYTHONPATH=../opendbc_repo scons board/obj/panda_h7.bin.signed
```

Pandaの既存更新処理は接続した機器の署名と同梱ファームウェアを比較する。このPRの作成中に実機へのインストール・フラッシュは行っていない。

## 検証

```sh
PYTHONPATH=opendbc_repo:. python -m unittest \
  opendbc.sunnypilot.car.nissan.tests.test_parking \
  opendbc.safety.tests.test_nissan_parking \
  openpilot.sunnypilot.selfdrive.selfdrived.tests.test_parking

PYTHONPATH=opendbc_repo:. python -m unittest discover \
  -s opendbc_repo/opendbc/safety/tests -p 'test_*.py'
```

合成CANで開始・終了・手動再許可、MADSの各状態、クルーズによる再有効化、通信途絶、CRC/カウンター異常、誤Bus、タイマー周回、リレー故障と実機同順序の転送を検証する。
別途ローカルの実ログで、PPP候補24,105フレームのCRCとホスト/ファームウェアの抑止判定を照合した。実ログ・動画・アカウント情報はリポジトリに含めない。

未検証: 実車での純正PPPの開始・継続・完了・中断、通常ProPILOTでの候補ビット、装置上の画面・通信全体の動作。これらの確認が終わるまではDraftのままレビューする。

## leaf-auto-resume ブランチ

`leaf-custom` から自動RES機能を追加したブランチ。対象は Leaf / Leaf IC、
`NissanAutoResume` は既定OFF。Sunnylink の Nissan Settings から設定し、次の走行で反映する。
純正ICCが有効な停車中、近距離の先行車を1秒間確認し、その車が1mを超えて離れ、
相対速度が0.5m/sを超えた最初の判定で、メーターがRES操作を要求している場合に
0.12秒のRESを停車1回につき一度だけ送る。先行車の消失や接近、相対速度の低下では保留した離脱判定を消す。

ICBMと自動RESは同じボタン送信処理を使う。自動RESが有効な停車中はICBMの速度調整と
保留ボタンを抑止し、運転者のボタン操作・キャンセルを優先する。運転者の操作は送信中の自動RESも中断する。
走行中、および自動RESがOFFの場合は既存のICBM処理を維持する。PPP抑止中は自動RESを含む
ホスト送信を停止し、ICBMの保留ボタンも消去する。

この移植では実機への配布・Pandaのフラッシュ・実車動作の検証は行っていない。
