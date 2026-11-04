# 実機・未予約通常起動の確認

2026-11-03。実機ブランチexperiment/leaf-session-probe、f93d82ed44。

READY後、request.json/inhibit.jsonがいずれも存在しないことを確認。card/controlsdが動作し、ControlsReady=1、Pandaはnissan安全設定（safetyParam=0、controlsAllowed=false）へ移行した。

約15秒・271サンプルの受信記録を保存。車両情報の受信、車速0、Pレンジ、ACC無効を確認した。別途の受信確認でcarControlも有効・enabled=false・longActive=falseだった。

未予約時に通常の起動経路を通る確認は完了。予備試験によるセッション移行要求・復帰要求は実行していない。通常の起動時ファームウェア照会とは区別する。

次は電源OFF・オフロードを確認してから、乗車・駐車条件を確認した上で一回限りの予備試験を予約する。試験予約は現在も未実施。
