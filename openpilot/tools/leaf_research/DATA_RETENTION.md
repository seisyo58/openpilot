# 原ログの保存状況と再取得の入口

2026-11-05、ユーザーから以下5ルートをcomma connectで保存したとの申告を受けた。Connectの保存フラグ・全rlogのアップロード完了・現在の取得可否は、こちらからライブ確認していない。

全て同じデバイス `0abcc72a352961a0` のルート。

|ルート|主な用途|解析時に扱った区間|
|---|---|---|
|[0000005c--6a84234ad4](https://connect.comma.ai/0abcc72a352961a0/0000005c--6a84234ad4)|e-Pedal OFF、駆動・制動・保持7例、装置CANCEL照合|全34区間|
|[00000059--02589b1b20](https://connect.comma.ai/0abcc72a352961a0/00000059--02589b1b20)|e-Pedal ON、要求符号化・保持・アクセル入力を伴う解除|全15区間|
|[0000005a--34bc682d3e](https://connect.comma.ai/0abcc72a352961a0/0000005a--34bc682d3e)|e-Pedal ON、別走行での比較|rlogは0/1/2/3/4/6/9/10/12/13/14。5/7/8/11は解析時欠損|
|[00000068--32fe1f4a1e](https://connect.comma.ai/0abcc72a352961a0/00000068--32fe1f4a1e)|75Dの通信有効化要求拒否、停止要求は未送信|segment0|
|[0000006c--4fcd507cdc](https://connect.comma.ai/0abcc72a352961a0/0000006c--4fcd507cdc)|拡張セッション後約3秒の受動観測|segment0|

今回の走行解析はローカルに残る抽出NPZを使用した。59の旧キャッシュ索引にある原rlog参照先45パスは、2026-11-05の再確認で全て不存在だった。保存済みルートから再取得できるかは別途確認が必要で、原rlogを失っても問題ないという意味ではない。

元NPZの所在は[source_inventory.json](source_inventory.json)、現在の再集計に使ったファイルのSHA256は[evidence](ARTIFACTS.md)の各監査JSONを参照。NPZは選択された信号しか含まない場合があり、carControlやPanda状態等の追加調査には原rlogが必要になる。
