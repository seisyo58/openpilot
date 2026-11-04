# 送信元・停止・復帰の追加調査

2026-11-04。今回の作業は公開資料、公開コード、既存の自車結果の照合のみ。車両への接続・要求・予約・設定変更は0。

## 結論

75DをACC/ADAS系として調べる根拠は維持されるが、1C3/2B0の物理送信元との対応、284E76WK2EのCommunicationControl対応、OFF後の復旧手順は今回も確定できなかった。新しい停止要求を実車へ追加する根拠は得られていない。

新たに、別所有者の2019年ZE1として整理されたLeafSpyの生ログと同時のECU一覧画像を取得した。707系と75D系を混同しないための追加の比較材料になる。

## 1. 新しく確認した公開生ログ

作者の元データ:
https://github.com/danjulio/can_logs/tree/847c1002b36b045af921558f4bcfe0307b612e83/2019_leaf_candumps/leafspy

ログ:
https://github.com/danjulio/can_logs/blob/847c1002b36b045af921558f4bcfe0307b612e83/2019_leaf_candumps/leafspy/driving/logfiles/leafspy_read_ecu_versions.crtd

対応画像:
https://github.com/danjulio/can_logs/blob/847c1002b36b045af921558f4bcfe0307b612e83/2019_leaf_candumps/leafspy/driving/pictures/leafspy_read_ecu_versions.png

- 707の21 83は727から肯定応答。ISO-TP再構成後の識別は5SA2ADBから始まる24バイトで、自車の6WK6ADBとは異なる。
- 707の21 84は肯定応答。データはASCII `2841918200340C1AB418`。対応画像ではFRONT CAMERAのExtra Informationにこの文字列がある。**28419で始まるからといって部品番号へ変換しない。** 画面のPN欄は別に28438、Version欄は5SA2Aと表示される。
- 同じログで75Dの21 83は7F 21 7F、22 F1 A1は7F 22 11。自車75Dの21 83拒否、F188肯定応答とは同一条件ではない。22 F1 A1と22 F1 88を同じ照会とも扱わない。
- この記録は707をカメラ系と呼ぶ追加の根拠になるが、LeafSpyの表示分類は所有者による物理配線追跡ではない。自車の707/75Dの物理分離や1C3/2B0送信元の確定には使えない。
- CRTDの1R11/2R11は記録側のチャネル表記。commaのbus1/2に対応付けない。
- ukodaの転載と作者元ログはバイト一致。ただし転載側の下位READMEに2017 AZE0とする不整合があり、年式の根拠は元の2019_leaf_candumps区分と転載上位の2019 ZE1区分に限定する。

再構成結果と関連16レコードは `leaf_public_2019_identity_audit.json` に保存。元ログSHA256: 35278007b20f79e4dc1e909529f0860a65f075b050ed58294aafc79ee195a87d。

21 84は公開記録にある読み出し候補ではあるが、通信停止・復帰の直接証拠にならないため、追加の小試験として実装・予約していない。

## 2. 日産公式資料で得られた調査経路

NTB10-066C（2024-05-20）はCONSULTのV-CAN画面とCAN Diag Support Monitorを使う診断を案内し、正しいCAN typeの選択と適合ESMの照合を求めている。
https://static.nhtsa.gov/odi/tsbs/2024/MC-11004960-0001.pdf

NTB13-027Dも車両のCAN構成と診断結果を使う切り分けを扱う。
https://static.nhtsa.gov/odi/tsbs/2024/MC-11004961-0001.pdf

これは実車の通信関係を絞る資料／画面の候補であり、75Dと数値ID1C3/2B0を対応付けられる保証はない。米国向け資料であり、日本2022仕様のCAN type・配線を確定しない。

NTB10-145BはABS/VDC診断時にDTCを診断前に消さず、関連系統の自己診断・CAN診断を記録することを指示する。
https://static.nhtsa.gov/odi/tsbs/2023/MC-10233896-0001.pdf

将来通信停止を試すなら、警告なしや対象フレームの再出現だけを事後確認にせず、DTC／CAN診断の前後記録も計画に含める根拠になる。ただし、今回DTCは読んでおらず、新しい読出し要求を実装したわけではない。

## 3. 最新の公開実装

GitHubをghで再取得。StarPilotのNissan interfaceの最新変更は既調査のcfd221b89cec223397f0a805791136aa9b958758。取得した現行内容でもLEAF_2025_SV_PLUS_ALPHA_LONG_ENABLED=False。停止経路は707の10 C0/28 02、復帰は10 81と対象CAN再出現の確認であり、75Dで成功した実装には変わっていない。
https://github.com/StarPilotHQ/StarPilot/commit/cfd221b89cec223397f0a805791136aa9b958758

今回の調査では新しい成功報告は確認できなかった。検索未発見は存在しないことの証明ではない。取得ファイルはwork/recovery_sources_20261104に保存した。

## 4. 次に入手価値がある情報

同じ状態読み出し試験を増やすより、次の資料の入手を優先する。

1. 日本2022年6月・ZE1-200001以降に適合するESMのLAN/CAN信号送受信表、ADAS/ICCのシステム構成図、ユニット位置・コネクタ・電源図。
2. 自車のCONSULTのICC/ADASとLane CameraそれぞれのECU識別情報、CAN Diag Support Monitor／V-CAN記録。対象車で取得可能かを先に確認する。
3. メーカー手順として通常通信の停止／再開を行う機能の有無。資料や画面に掲載されない場合、ESM購入だけで要求バイトが判明するとは期待しない。

日産公式FAQは整備要領書・配線図集等を販売店で注文できると案内するが、個別資料の販売可否・収録内容・価格は未確認。
https://faq2.nissan.co.jp/faq/show/11265?category_id=68&site_domain=default

販売店への連絡や資料購入は今回行っていない。車台番号全体をチャットへ提示する必要もない。必要なら所有者が販売店へ直接提示して適合確認する。
