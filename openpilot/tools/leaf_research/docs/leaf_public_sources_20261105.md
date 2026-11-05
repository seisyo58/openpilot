# 送受信ECU・停止仕様の公開一次資料と記録FWの追加照合

2026-11-05。gh・公開Web・復旧原rlogを照合した。連絡・購入・車両通信・コード配備はしていない。

## 1. 新しい移植元や同型ECUの停止成功例は得られなかった

[StarPilotの固定版Nissan interface](https://github.com/StarPilotHQ/StarPilot/blob/cfd221b89cec223397f0a805791136aa9b958758/opendbc_repo/opendbc/car/nissan/interface.py)をghで再取得。最終変更は既調査と同じ2026-08-23の `cfd221b89cec223397f0a805791136aa9b958758`、ファイルSHA256も既調査の `a03378ee0227262032d47911b319080c2a2b36968e7dee72ec30e8ba16099a1d` と同じだった。

- 2025 SV Plusの特定camera FWを対象にする条件。
- 既存の機能ゲートはFalseで、判定関数でもそのゲートを使用する。
- 通信停止候補は707/bus0のKWP経路。75Dで成功した仕様ではない。

別の公開リポジトリ[nkaoud-sp/openpilot-stp](https://github.com/nkaoud-sp/openpilot-stp/blob/56d6a6ee479f7599e4aa58e1af825a241d3b8f24/opendbc_repo/opendbc/car/nissan/interface.py)にも同系統の実装が見つかった。ただし最終変更は2026-08-21の `56d6a6ee479f7599e4aa58e1af825a241d3b8f24`。この同一コミットはStarPilot側にも存在する。新しい2022年車の独立した成功例として数えない。

公開実装が要求するFWの先頭は6WK2CDB。自車の記録は6WK6ADBで異なる。この条件を外せば適合するという根拠はない。ゲートやFW条件を緩める実装変更は行っていない。

## 2. 原ログのcarFwに追加の手掛かりはあるか

[audit_firmware_inventory.py](../audit_firmware_inventory.py)で59/5a/5cのsegment0に記録されたcarParams.carFwを再取得した。[監査JSON](../evidence/leaf_firmware_inventory_20261105.json)には長さ・SHA256・要求・経路情報だけを保存し、VINやFW全文は出力しない。

3ルートの記録は共通して、ソフト上のeps/742、fwdCamera/707、gateway/18DAD0F1、abs/740という分類を含む。fwdCameraの24byte識別は全て同じSHA256で、先頭は6WK6ADB。これらのラベルはパーサー/照会定義による分類であり、メーカーが普通CANの送信元を示した表ではない。

重要なのは**bus1のcarFw項目がobdMultiplexing=trueであること**。bus0項目はfalse。従って、同じFWをbus0/bus1で記録したことを、そのまま通常利用中の非多重化bus1への直接照会成功と扱わない。これまでの[bus1・75D識別試験](leaf_bus1_identity_attempt_001.md)とは照会条件が違う。

gatewayの同一識別が同じbusに重複する項目もある。記録件数を物理ECU数に変換しない。carFwはキャッシュ由来の場合もあり、この時点の新鮮で網羅的な実車スキャンを意味しない。

この追加照合でも、707と75Dの物理ユニット対応、1C3/2B0/1D6の物理送信元・受信先は確定しなかった。

## 3. 日本向け取扱説明書で確認できる優先関係

[日産公式2206区分のPDF](https://www.nissan.co.jp/SP/OM/LEAF/2206/manual_t00um5sa7a.pdf)の印刷P.199を、保存済みPDFからページ全体を描画して目視した。ファイルSHA256は `83f0d1ef382893600fb51f420f7db33105a5382eb149d0e601c36145d59226e9`。版の適合留保は[従来の資料照合](leaf_2022_model_and_cancel_findings.md)を引き継ぐ。

取扱説明書はクルーズ/プロパイロット等の運転支援がe-Pedalより優先する旨を説明する。従って、2F4/5B3が設定ONでも、ACC中の減速要求がe-Pedal経路から出るとはいえない。観測上ACC制動入口で1D6が一定だったことと矛盾する資料ではない。

これは操作仕様の優先関係であり、1D6のCAN送信元・受信先や要求受理を特定する記載ではない。e-Pedal設定ON/OFFによる境界値の差を、単独の因果として確定する資料にもならない。

## 4. 適合サービス資料の探索範囲と限界

完全一致の284E76WK2E/284E7-6WK2E、75DのCommunicationControl、Nissanの1C3/2B0、公式サイトのZE1/CAN/ADASを検索した。中古部品の完全一致表記は見つかったが、物理位置・数値CAN ID・診断仕様の一次根拠には採用しない。

[日産TechInfo](https://www.nissan-techinfo.com/)や、[日産クリエイティブサービスの整備マニュアル制作/問合せ業務](https://www.nissan-nics.co.jp/service/carlifesupport/)も確認した。検索に現れた公開の旧サービス資料カタログはC27向けで、今回適合するZE1のCAN信号送受信表を得たものではない。カタログホストのrootは403で、制限先への迂回・アカウント作成等は試みていない。

探索した範囲で、同型ECUの停止/復帰成功手順や、日本2022年6月車に適合する数値CAN送受信表は得られなかった。未発見は資料の不存在やソフト経路の不可能性を証明しない。非公開・未索引の情報や正規の適合資料が別にある可能性は残る。

検索対象・取得ファイル版・ハッシュ・除外した情報の扱いは[公開資料監査JSON](../evidence/leaf_public_source_audit_20261105.json)に保存した。署名URLや認証情報は含めない。

## この論点の判断

現時点で得られる公開根拠から、次の実車停止要求・引数探索・既存実装の条件解除を追加する理由はない。次の入口は、同じECUの正規停止/復帰の成功記録、適合するユニット/CAN構成資料、または実車の物理送受信点を特定できる新しい証拠。

購入・販売店への相談を必須の既定路線にはしない。既存の[確認項目](leaf_service_information_scope.md)と[依頼文案](leaf_service_information_request.md)は、必要な情報を整理した未送信資料として残す。

FW在庫の再現:

```sh
PYTHONPATH=opendbc_repo .venv/bin/python -m openpilot.tools.leaf_research.audit_firmware_inventory \
  /path/to/raw_archive/00000059--02589b1b20/0/rlog.zst \
  /path/to/raw_archive/0000005a--34bc682d3e/0/rlog.zst \
  /path/to/raw_archive/0000005c--6a84234ad4/0/rlog.zst \
  --out /tmp/leaf-firmware-inventory.json
```
