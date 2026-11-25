# 成果物と参照先

知識の入口は[引き継ぎ文書](HANDOFF.md)。以下はこのブランチに実際に含まれる資料の索引。

[原ログの保存状況](DATA_RETENTION.md): 2026-11-05にユーザーがConnectで保存した5ルート。Preservedフラグはユーザー申告で、原rlog66件の取得・読み取り・ハッシュは後続で確認済み。

[調査項目一覧](INVESTIGATION_STATUS.md)、[終了監査と再開条件](COMPLETION_AUDIT.md)、[原rlog復旧後の最新結果](docs/leaf_raw_recovery_20261105.md)を参照。取得・解析元66区間の照合と5aの欠損解消は実施済み。

## 調査履歴に対するレビュー

- [2026-11-05: Codexの見解と次のアプローチ](docs/leaf_longitudinal_review_20261105.md) — 既存の観測・仮説・提案を区別したレビュー。新たな実車試験結果ではない。
- [2026-11-05: 3経路の接続関係と1D6の再集計](docs/leaf_transport_paths_20261105.md) — 受信抽出NPZを3走行で再監査。1D6のbus0/1継続とbus2起動直後の記録を整理。
- [2026-11-05: 通常要求・協調制動・保持・解除の観測分類](docs/leaf_event_classification_20261105.md) — 保持候補13区間、観測できた解除12例をRES・装置CANCEL等の履歴と照合。
- [2026-11-05: 特別パターンの境界比較](docs/leaf_boundary_audit_20261105.md) — OFF側を含む45入口・38出口。複数エッジと同一Event内の変化を区別。

## 実機試験結果

- [leaf_bus1_identity_attempt_001](docs/leaf_bus1_identity_attempt_001.md)
- [leaf_extended_observation_attempt_001](docs/leaf_extended_observation_attempt_001.md)
- [leaf_identity_attempt_001](docs/leaf_identity_attempt_001.md)
- [leaf_identity_batch_attempt_001](docs/leaf_identity_batch_attempt_001.md)
- [leaf_passive_session_attempt_001](docs/leaf_passive_session_attempt_001.md)
- [leaf_session_observation_attempt_001](docs/leaf_session_observation_attempt_001.md)
- [leaf_session_probe_attempt_002](docs/leaf_session_probe_attempt_002.md)
- [leaf_suppression_attempt_001](docs/leaf_suppression_attempt_001.md)
- [leaf_uds_communication_attempt_001](docs/leaf_uds_communication_attempt_001.md)
- [leaf_uds_session_attempt_001](docs/leaf_uds_session_attempt_001.md)

## その他の調査・途中の計画

古い仮説や準備状態を含む。最新の結論はHANDOFF.mdと後続の実測報告を優先する。

- [leaf_2022_model_and_cancel_findings](docs/leaf_2022_model_and_cancel_findings.md)
- [leaf_284e76wk2e_followup](docs/leaf_284e76wk2e_followup.md)
- [leaf_2aa_state_findings](docs/leaf_2aa_state_findings.md)
- [leaf_5c_findings](docs/leaf_5c_findings.md)
- [leaf_acc_brake_switching](docs/leaf_acc_brake_switching.md)
- [leaf_acc_cross_route_findings](docs/leaf_acc_cross_route_findings.md)
- [leaf_acc_investigation](docs/leaf_acc_investigation.md)
- [leaf_adas_tx_suppression_plan](docs/leaf_adas_tx_suppression_plan.md)
- [leaf_bounded_gyro_check](docs/leaf_bounded_gyro_check.md)
- [leaf_braking_coordination_findings](docs/leaf_braking_coordination_findings.md)
- [leaf_bus1_identity_preparation](docs/leaf_bus1_identity_preparation.md)
- [leaf_comma_ready_validation](docs/leaf_comma_ready_validation.md)
- [leaf_daily_use_and_uds_stop_preparation](docs/leaf_daily_use_and_uds_stop_preparation.md)
- [leaf_diagnostic_identity_followup](docs/leaf_diagnostic_identity_followup.md)
- [leaf_diagnostic_path_followup](docs/leaf_diagnostic_path_followup.md)
- [leaf_drive_and_regen_findings](docs/leaf_drive_and_regen_findings.md)
- [leaf_handover_design](docs/leaf_handover_design.md)
- [leaf_hold_design_assessment](docs/leaf_hold_design_assessment.md)
- [leaf_hold_mode_assessment](docs/leaf_hold_mode_assessment.md)
- [leaf_hold_path_findings](docs/leaf_hold_path_findings.md)
- [leaf_identity_batch_plan](docs/leaf_identity_batch_plan.md)
- [leaf_identity_batch_ready](docs/leaf_identity_batch_ready.md)
- [leaf_identity_experiment_next](docs/leaf_identity_experiment_next.md)
- [leaf_identity_probe_ready](docs/leaf_identity_probe_ready.md)
- [leaf_identity_uds_probe_ready](docs/leaf_identity_uds_probe_ready.md)
- [leaf_long_request_calibration](docs/leaf_long_request_calibration.md)
- [leaf_longitudinal_branch_status](docs/leaf_longitudinal_branch_status.md)
- [leaf_longitudinal_transport_findings](docs/leaf_longitudinal_transport_findings.md)
- [leaf_matched_braking_and_imu_findings](docs/leaf_matched_braking_and_imu_findings.md)
- [leaf_minimal_hardware_options](docs/leaf_minimal_hardware_options.md)
- [leaf_observation_update](docs/leaf_observation_update.md)
- [leaf_recovery_candidates_followup](docs/leaf_recovery_candidates_followup.md)
- [leaf_recovery_review_20261104](docs/leaf_recovery_review_20261104.md)
- [leaf_sender_and_recovery_followup_20261104](docs/leaf_sender_and_recovery_followup_20261104.md)
- [leaf_service_information_request](docs/leaf_service_information_request.md)
- [leaf_service_information_scope](docs/leaf_service_information_scope.md)
- [leaf_session_broadcast_findings](docs/leaf_session_broadcast_findings.md)
- [leaf_session_observation_probe_plan](docs/leaf_session_observation_probe_plan.md)
- [leaf_session_probe_implementation](docs/leaf_session_probe_implementation.md)
- [leaf_session_probe_ready](docs/leaf_session_probe_ready.md)
- [leaf_stationary_engage_findings](docs/leaf_stationary_engage_findings.md)
- [leaf_stationary_epedal_switch_findings](docs/leaf_stationary_epedal_switch_findings.md)
- [leaf_suppression_trial_ready](docs/leaf_suppression_trial_ready.md)
- [leaf_switch_deep_findings](docs/leaf_switch_deep_findings.md)
- [leaf_switch_rule_findings](docs/leaf_switch_rule_findings.md)
- [leaf_total_deceleration_request](docs/leaf_total_deceleration_request.md)
- [leaf_uds_communication_probe_plan](docs/leaf_uds_communication_probe_plan.md)
- [leaf_uds_communication_probe_ready](docs/leaf_uds_communication_probe_ready.md)
- [leaf_uds_session_probe_plan](docs/leaf_uds_session_probe_plan.md)
- [leaf_uds_session_probe_ready](docs/leaf_uds_session_probe_ready.md)
- [leaf_unarmed_startup_validation](docs/leaf_unarmed_startup_validation.md)

## 採録した監査・照合データ

- [leaf_public_source_audit_20261105.json](evidence/leaf_public_source_audit_20261105.json) / [leaf_firmware_inventory_20261105.json](evidence/leaf_firmware_inventory_20261105.json) — [公開一次資料と記録FWの追加照合](docs/leaf_public_sources_20261105.md)。[audit_firmware_inventory.py](audit_firmware_inventory.py)で記録FWを再現。
- [leaf_recovered_holdout_20261105.json](evidence/leaf_recovered_holdout_20261105.json) — [復旧未使用区間での固定評価と要求/応答の限界](docs/leaf_response_limits_20261105.md)。[check_recovered_holdout.py](check_recovered_holdout.py)で再現。
- [leaf_raw_signals_20261105.json](evidence/leaf_raw_signals_20261105.json) — [復旧後の符号化・カウンター・returned監査](docs/leaf_raw_signals_20261105.md)。[audit_raw_signals.py](audit_raw_signals.py)で再現。
- [leaf_raw_recovery_20261105.json](evidence/leaf_raw_recovery_20261105.json) — 原rlog66区間のハッシュ・再抽出・欠損解消後の分類・carControl・設定情報。旧NPZの欠損を含む結果より優先。
- [leaf_transport_visibility_20261105.json](evidence/leaf_transport_visibility_20261105.json) — [audit_transport.py](audit_transport.py)で元NPZを再集計したバス別受信数・観測範囲・SHA256。
- [leaf_event_classification_20261105.json](evidence/leaf_event_classification_20261105.json) — [classify_events.py](classify_events.py)で元NPZを再集計した観測分類・区間一覧・解除履歴。
- [leaf_boundary_audit_20261105.json](evidence/leaf_boundary_audit_20261105.json) — [audit_boundaries.py](audit_boundaries.py)による特別パターンの境界・近傍エッジ全候補・観測時刻区間の比較。
- [leaf_2aa_state_dbc_checks.json](evidence/leaf_2aa_state_dbc_checks.json)
- [leaf_bounded_gyro_check.json](evidence/leaf_bounded_gyro_check.json)
- [leaf_bus1_identity_attempt_001_rlog_audit.json](evidence/leaf_bus1_identity_attempt_001_rlog_audit.json)
- [leaf_diagnostic_bus_visibility.json](evidence/leaf_diagnostic_bus_visibility.json)
- [leaf_drive_dbc_checks.json](evidence/leaf_drive_dbc_checks.json)
- [leaf_extended_observation_attempt_001_rlog_audit.json](evidence/leaf_extended_observation_attempt_001_rlog_audit.json)
- [leaf_identity_attempt_001_rlog_audit.json](evidence/leaf_identity_attempt_001_rlog_audit.json)
- [leaf_identity_batch_attempt_001_rlog_audit.json](evidence/leaf_identity_batch_attempt_001_rlog_audit.json)
- [leaf_observer_dbc_checks.json](evidence/leaf_observer_dbc_checks.json)
- [leaf_passive_session_attempt_001_rlog_audit.json](evidence/leaf_passive_session_attempt_001_rlog_audit.json)
- [leaf_passive_session_broadcast_scan.json](evidence/leaf_passive_session_broadcast_scan.json)
- [leaf_public_2019_identity_audit.json](evidence/leaf_public_2019_identity_audit.json)
- [leaf_session_broadcast_scan.json](evidence/leaf_session_broadcast_scan.json)
- [leaf_session_observation_attempt_001_rlog_audit.json](evidence/leaf_session_observation_attempt_001_rlog_audit.json)
- [leaf_session_probe_attempt_002_rlog_audit.json](evidence/leaf_session_probe_attempt_002_rlog_audit.json)
- [leaf_stationary_epedal_switch_results.json](evidence/leaf_stationary_epedal_switch_results.json)
- [leaf_suppression_attempt_001_rlog_audit.json](evidence/leaf_suppression_attempt_001_rlog_audit.json)
- [leaf_uds_communication_attempt_001_rlog_audit.json](evidence/leaf_uds_communication_attempt_001_rlog_audit.json)
- [leaf_uds_session_attempt_001_rlog_audit.json](evidence/leaf_uds_session_attempt_001_rlog_audit.json)

## このブランチに含めていない原資料

元ワークスペースに残る解析スクリプト・図・npz・rlog等の選定一覧は[source_inventory.json](source_inventory.json)を参照。現在の所在を記録したもので、全ファイルのコピーではない。

診断試験の実行コードと模擬試験は、同じGitリポジトリの固定コミット `a125cb9708e1d02c3afecd23734d620acf51ccda` の `openpilot/tools/leaf_longitudinal/` に保存されている。この保存用ブランチへは取り込んでいない。

会話の逐語録や未採録のレビュー本文は含まれない。元データがない環境では、監査JSONの結果は読めても同じ解析を全て再実行できるとは限らない。
