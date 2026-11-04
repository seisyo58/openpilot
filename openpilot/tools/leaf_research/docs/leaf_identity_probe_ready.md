# 識別読み出し試験の準備

2026-11-03。車両OFFで準備。試験はまだ予約・実行していない。

- PC実装：28afb42123、試験書式修正：f3f8d9cbb2。
- 実機：experiment/leaf-session-probe、4851ad4966ce5fee6d2a9a834492140dd82a904d。作業ツリーはクリーン。
- PC・commaとも全70件の模擬試験が通過。変更箇所のRuffも通過。実車応答を確認した結果ではない。
- 実機バックアップ：/data/leaf-identity-backup-f3f8d9cbb2。
- 車両OFF、最新deviceState/pandaStates/managerStateのvalid/alive確認。Panda noOutput、ignitionLine/ignitionCan false、faultsなし。OFF時のCAN0エラーカウンターには履歴・errorPassive表示があり、これだけで走行時故障とは判断しない。新しい識別アダプターは実行時に通信エラー状態を検知すると送信を拒否する。
- 試験予約request.json、抑止inhibit.jsonはいずれもなし。停止候補は無効のまま。

識別用の21 83を0x75D/bus 0へ最大1回。仮説の応答先0x77Dから識別サービスの有効な先頭フレームを受けたときだけ、フロー制御を最大1回送る。読み出し後のセッション変更、停止、リセット、DTC消去、Tester Present、アクチュエーター操作、別バス・宛先・サービスへの自動再試行はない。

正常な起動時のファームウェア照会は別に存在する。上記最大2フレームは今回の専用試験の追加送信範囲を示すもので、起動全体の送信数ではない。

実行には、操作員が車内でP・駐車ブレーキ・ブレーキ保持の準備を確認した後、OFFで一度限りの予約を入れ、60秒以内にREADYにする手順が必要。全結果で通常制御への移行を抑止し、確認後OFFにして記録を監査する。まだこの予約操作は行っていない。
