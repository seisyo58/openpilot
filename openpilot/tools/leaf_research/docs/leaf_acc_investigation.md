# リーフICのアクセル・ブレーキ直接制御：受信ログ調査

調査日：2026-11-03。車両への送信、診断セッション変更、制御ソフトの変更は実施していない。

## 結論

直接制御を実現できる可能性を裏付ける、有力な信号と公開実験実装が見つかった。Bus 1の `0x2B0` は駆動・回生要求、`0x1C3` は摩擦ブレーキ要求の候補として優先して調べる価値がある。

あなたの車の受信ログと公開実装の符号化構造は一致する。ただし、車両が置き換えた指令を受け入れること、正確な物理単位、停止保持、故障時の復帰、AEBとの共存は未確認。現時点で走行用の移植を正当化するものではない。

## 使用した記録と比較条件

- 主解析：`0abcc72a352961a0/00000059--02589b1b20` の全15区間のrlog。ユーザー申告ではe-Pedal使用。
- `carParams.carFingerprint` は `NISSAN_LEAF_IC`、openpilotによる縦制御は無効。
- ACC有効・無効が同じ走行内に含まれる。最初の有効化は先頭CANフレームから約134.04秒後。
- ACC有効区間はおおよそ134–387秒、413–465秒、502–513秒、528–722秒。これは `carState.cruiseState.enabled` であり、下記の要求有効フラグと完全に同じ状態ではない。
- 先に指定された `00000058--eb41dfe7a2` は、取得した全区間のqlogでACC有効サンプルがゼロだった。CANが間引かれているため、波形比較の主資料には使っていない。
- e-Pedal状態そのもののCANビットは今回同定していない。e-Pedalの影響を独立に除去したとはいえないが、同一路線内のACC有効・無効を比較することで、別走行同士だけの比較を避けた。

## 1. Bus 1 / 0x2B0：駆動・回生要求の有力候補

86,888フレームを観測した。公開fork StarPilotは、このメッセージを `Leaf ADAS propulsion/regen request` として生成する。

同実装に従い、0始まりのバイト番号で次のように読み取った。

```text
raw12 = (byte[1] << 4) | (byte[2] & 0x0f)
fraction = (byte[7] >> 2) & 0x03
raw_command = raw12 * 4 + fraction
active_candidate = (byte[4] & 0x40) != 0
```

観測結果：

- 反転値の冗長符号が86,888 / 86,888フレームで一致。
- fractionとその反転値も86,888 / 86,888フレームで一致。
- 要求有効フラグがOFFの40,465フレームでは、`raw_command` は例外なく5320。
- 要求有効フラグがONの範囲は2518–9215。2518は2,248フレームに現れ、ブレーキ要求との併用が多い。
- ACC解除に伴って要求が5320へ戻る変化を複数回確認。ACC有効でも、停止・オーバーライド等を含む状態により要求有効フラグがOFFになることがあるため、両フラグを同一視しない。

解除中に固定値へ戻る点と、公開実装との符号化一致から、単なる実測加速度よりも制御要求の候補として強い。ただし、この観測だけで送信元ECU・受信先・受理条件を確定したことにはならない。

StarPilotは通常要求に `(raw_command - 6144) / 2048` という加速度換算を使う。しかし、今回のログによる物理校正は未実施。特別値2518や非作動値5320をそのままこの式に通してはならない。図は物理加速度に換算せずraw値で示した。追加調査で通常値のビット重み付けにも再検証が必要な点を確認したため、反転値の一致を加速度の数値化の正しさと同一視しない。詳細は `leaf_acc_brake_switching.md` を参照。

## 2. Bus 1 / 0x1C3：制動要求の有力候補（摩擦専用かは未確定）

86,888フレームを観測した。公式DBCにも `PROPILOT_BRAKE` があるが、公開実験実装では圧力を10ビットとして扱う。公式の9ビット定義と区別して解析した。公開実装は摩擦ブレーキ要求と呼ぶが、摩擦専用であることは未確認。配分前要求の追加調査は `leaf_total_deceleration_request.md` を参照。

```text
pressure_raw10 = ((byte[0] & 0x3f) << 4) | (byte[1] >> 4)
brake_active_candidate = (byte[5] & 0x80) != 0
checksum = (0x01 + 0xc3 + sum(byte[0:7])) & 0xff
```

観測結果：

- チェックサムは86,888 / 86,888フレームで一致。
- 圧力raw値の範囲は0–829。物理圧力への換算は未確認。
- 圧力が正のフレームは2,350件。作動フラグとの不一致はゼロ。
- `carState` の直前サンプルと対応させた比較では、ACC無効中に作動フラグが立ったフレームはゼロ。
- 約423.88–429.01秒では、圧力raw値が上昇する間に約44.8 km/hから約5.4 km/hへ減速。
- 約435.50–438.76秒ではほぼ停止した状態でも値が現れ、最大829を記録。停止保持に関係する可能性があるが、役割は未確定。
- 圧力作動2,350件のうち、直前の `0x2B0` が特別値2518だったものは2,221件。駆動・回生側とブレーキ側の協調状態を示唆する。

## 3. 状態報告と要求信号を分ける

Bus 1の `0x33D` は加速度との相関が高いものの、手動運転中も同様に変動し、最も相関する時刻は実測加速度より約0.6秒遅い。この結果だけなら、指令として扱う根拠は弱い。

公開されているEV-CANのモータートルク要求 `0x1D4` と応答 `0x1DA` は、今回解析した受信Bus 0・1・2では観測されなかった。これは現在の接続・記録範囲の結果であり、車両内に存在しないことを意味しない。EV-CANへの別接続経路と、ADASからの要求経路を分けて考える必要がある。

## 4. 公開実装はそのまま移植できない

StarPilotの参照版は `febcb8f290782084c556472b6adcb4c3b0ff2fc7`。

- 機能は `LEAF_2025_SV_PLUS_ALPHA_LONG_ENABLED = False` で無効化されている。
- 対象条件は `NISSAN_LEAF` と特定の2025 SV Plus向けADASファームウェア。あなたのログは `NISSAN_LEAF_IC`。
- 公開実装の対象ファームウェア識別文字列は `6WK2CDB`。今回のログの0x707は `6WK6ADB`。後続バイトも異なる。
- 同実装は純正ADASの通常送信を診断経由で停止し、無通信の確認後に指令を生成する設計。受信できることだけでは、この引き継ぎが可能かどうかはわからない。
- 今回の圧力raw値の最大829は、同実装の上限659を超える。829を新しい送信上限として採用すべきという意味ではない。
- 同実装が `brake_mode` と呼ぶビットは、今回4,713フレームで圧力ゼロのまま立っていた。同実装の送信側安全条件はその組み合わせを拒否するため、純正の全状態を再現する仕様ではない。
- `0x2B0` の所有状態byte[3]は0x4C・0x6C・0x8C・0xACを観測したが、同実装の送信側では0xACを前提とする。

これらは公開実装の存在を否定するものではなく、対象車・状態・制御範囲を限定した実験コードであることを示す。公開コードから実車検証済みの完成機能とは判断できない。

## 次に調べるべきこと

1. 既存の別ACC走行でも、冗長符号・解除時の固定値・制動時の協調を再確認する。e-Pedal OFFのACCログが既にあれば比較に有用。
2. `0x2B0` の特別値・所有状態・有効フラグと、停止・発進・ペダル介入の対応を整理する。実測との相関だけで単位や意味を断定しない。
3. 車両の該当年式・ADAS識別情報に対応する配線資料で、Bus 1の接続先と指令経路を確認する。
4. 送信の検証が必要になった段階では、非走行のベンチ環境で純正送信との競合、途絶時の復帰、ペダル優先、AEBへの影響を別途確認する。今回の調査では車両送信はしていない。

## 添付ファイル

- `leaf_acc_signal_evidence.png`：全走行と制動・停止部分の拡大図。
- `leaf_longitudinal_candidates.dbc`：Cabanaで受信データを観察するための候補定義。Bus 1を選択する。既存車両DBCの代替ではなく、物理単位を確定しないraw値の定義。

## 追補：StarPilotで無効化された経緯

公開コミット履歴・各コミットの関連PRとコメント・公開ドキュメントを確認した。以下の日付は日本時間。

- 2026-08-09：`a59f46c7b2` でLeaf / Leaf IC向けの縦制御実装を追加。
- 2026-08-15：`c2e2c24e8c` で対象を2025 SV Plusの特定ADASファームウェアに限定し、Leaf ICを対象外に変更。
- 2026-08-19：`5d9b55adcb` と `4078e6fcfa` で、純正ADASの指令送信が停止したことの確認、失敗時の復元、純正送信が復帰したことの確認を追加・修正。
- 2026-08-22：`56d6a6ee47` で診断セッションと送信停止要求を変更。応答そのものを必須にせず、停止確認処理と組み合わせる方式に変更。
- 2026-08-24：`cfd221b89c` で `LEAF_2025_SV_PLUS_ALPHA_LONG_ENABLED = False` を追加。通常動作では縦制御もECU停止処理も行わないことをテストで保証し、内部実装のテストだけでフラグを一時的に有効化する形に変更。

無効化コミットのテストには、実装を通常利用に提供せず休止状態として試験する趣旨のコメントがある。ただし、コミットメッセージは `fine day, Sunday.` であり、無効化の具体的理由は書かれていない。確認した7件のコミットには関連PR・コミットコメントがなかった。公開資料から、実車で何が起きたために無効化したかは確定できない。

履歴から、純正ADASの送信停止と復帰を含む制御引き継ぎが重点的に修正されていたことはわかる。これが無効化の直接原因だったというのは推測にとどまる。

参照：[初期実装](https://github.com/StarPilotHQ/StarPilot/commit/a59f46c7b2f2d01d9e941751b54dc0c3e8556aa2)、[対象の限定](https://github.com/StarPilotHQ/StarPilot/commit/c2e2c24e8c7b414d8bbc157e71682d2c00cbe82a)、[停止・復帰確認](https://github.com/StarPilotHQ/StarPilot/commit/5d9b55adcb9a7577e2c7ced30c13e211bdaf6fec)、[復帰処理の修正](https://github.com/StarPilotHQ/StarPilot/commit/4078e6fcfad37faf74d54a692114d33661de5473)、[診断方式変更](https://github.com/StarPilotHQ/StarPilot/commit/56d6a6ee479f7599e4aa58e1af825a241d3b8f24)、[無効化](https://github.com/StarPilotHQ/StarPilot/commit/cfd221b89cec223397f0a805791136aa9b958758)。

## 公開資料

- [StarPilot：要求メッセージの生成](https://github.com/StarPilotHQ/StarPilot/blob/febcb8f290782084c556472b6adcb4c3b0ff2fc7/opendbc_repo/opendbc/car/nissan/nissancan.py)
- [StarPilot：対象車両・無効化フラグ・引き継ぎ処理](https://github.com/StarPilotHQ/StarPilot/blob/febcb8f290782084c556472b6adcb4c3b0ff2fc7/opendbc_repo/opendbc/car/nissan/interface.py)
- [StarPilot：制御量と制動の組み合わせ](https://github.com/StarPilotHQ/StarPilot/blob/febcb8f290782084c556472b6adcb4c3b0ff2fc7/opendbc_repo/opendbc/car/nissan/carcontroller.py)
- [StarPilot：パラメーター](https://github.com/StarPilotHQ/StarPilot/blob/febcb8f290782084c556472b6adcb4c3b0ff2fc7/opendbc_repo/opendbc/car/nissan/values.py)
- [StarPilot：送信側安全条件](https://github.com/StarPilotHQ/StarPilot/blob/febcb8f290782084c556472b6adcb4c3b0ff2fc7/opendbc_repo/opendbc/safety/modes/nissan.h)
- [公式Nissan DBC](https://github.com/commaai/opendbc/blob/master/opendbc/dbc/generator/nissan/_nissan_common.dbc)
- [公式Nissan送信側安全条件](https://github.com/commaai/opendbc/blob/master/opendbc/safety/modes/nissan.h)
- [ZE1 EV-CANの公開DBC](https://github.com/dalathegreat/leaf_can_bus_messages/blob/main/EV-can_ZE1.dbc)
