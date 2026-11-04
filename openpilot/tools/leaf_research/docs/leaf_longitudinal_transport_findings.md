# Nissan A longitudinal transport feasibility

Investigation: 2026-11-03. User vehicle: ZE1 e+ G, June 2022 (user report). User confirmed the ordinary Nissan A harness without wiring modifications. The exact harness-box revision has not been physically inspected.

## Result

The usual bus 0/2 forwarding replacement mechanism cannot replace the observed longitudinal frames on bus 1 with this harness. A software transmit allowlist change alone does not solve stock/host coexistence. This is not proof that every stock-wiring injection protocol is impossible; receiver arbitration and any stock-transmitter suppression mechanism are unknown.

## Hardware evidence

Official comma hardware repository, commit `3e04f5c2a7c4309e90e60ff65f0e92b47a7d256f`:

- [Harness construction guide](https://github.com/commaai/hardware/blob/3e04f5c2a7c4309e90e60ff65f0e92b47a7d256f/harness/BUILD_HARNESS.md): CAN0/CAN2 can be separated by the harness-box relay. CAN1 is a read/write tap with the original path passed through; it cannot be intercepted by that relay.
- [Nissan A v3 wiring](https://github.com/commaai/hardware/blob/3e04f5c2a7c4309e90e60ff65f0e92b47a7d256f/harness/v3/Nissan_A_Harness.pdf): separate CAN0/CAN2 pairs, CAN1 pairs passed through via the harness interface. Inspected rendered diagram, not just extracted text.
- [Nissan A v1 wiring](https://github.com/commaai/hardware/blob/3e04f5c2a7c4309e90e60ff65f0e92b47a7d256f/harness/v1/Nissan_A_Harness.sch.pdf): same relevant CAN0/2 vs CAN1 topology. Inspected rendered diagram.

These are Panda logical bus names. `panda/board/drivers/can_common.h` maps Panda bus 1 to its second CAN controller. Do not confuse this with the hardware guide's logical CAN0/1/2 labels or rename it based on controller numbering. Harness orientation swaps logical 0/2, not bus 1.

## Recorded evidence

|Route|0x2B0 bus 1 frames|0x1C3 bus 1 frames|
|---|---:|---:|
|59, e-Pedal ON|86888|86888|
|5a, e-Pedal ON, incomplete segments|63322|63322|
|5c, e-Pedal OFF|200812|200812|

Both IDs were observed only on bus 1 in the extracted records. No interpolation across missing 5a segments. This does not identify the transmitter ECU or prove there are no other buses elsewhere in the car. It does show that a bus 0/2 packet replacement is not the recorded transport for these candidates.

## Software evidence

Sources inspected on experiment/leaf-longitudinal, baseline leaf-custom 7b28fbe1eb:

- `opendbc_repo/opendbc/safety/safety.h`, `get_fwd_bus`: 0→2, 2→0, otherwise -1. Returning blocked from a forwarding hook only stops forwarding, not an original frame on an uninterrupted wire.
- `opendbc_repo/opendbc/safety/modes/nissan.h`: both Leaf TX allowlists omit 0x2B0/0x1C3. Existing replacement targets concern steering/HUD/buttons; no longitudinal bus 1 replacement exists.
- `panda/board/boards/tres.h`, `tres_set_can_mode`: selects a CAN controller/transceiver connection; selecting a mode does not create a second independently isolated endpoint on the passed-through bus 1 network.
- Saved historical StarPilot code constructs these IDs for bus 1. That confirms its intended output destination only; it does not demonstrate elimination of stock transmitters, successful coexistence, or vehicle validation on this car.

## Consequences for the next experiment

Do not interpret any of the following as stock takeover: CAN ACK, observed sendcan, Panda TX receipt, or a raw quantity changing after host activity. They do not establish which ECU accepted a request, which request prevailed, or how release works.

Two possible engineering paths remain, neither implemented:

1. **A verified same-network protocol.** Establish documented/measured receiver selection and stock-transmitter behavior, plus request expiry and release. No such protocol has been identified. Do not assume sending more often wins. An ECU communication-disable diagnostic command is not a verified workaround; dependencies and recovery are unknown.
2. **An isolated interception path.** Identify the actual sender/receiver and network topology from the matching vehicle wiring/service documentation, then design a reversible, correctly terminated interception arrangement with a hardware fallback and bench validation. Existing Panda 0/2 connectivity is already used for the steering-side path; moving bus 1 to it is not a simple software-only extension and could disrupt current steering/PPP/AEB behavior. Additional interfaces may be required, but no specific purchase or vehicle rewiring is recommended from current evidence.

Next evidence needed is the 2022 Japanese ZE1's ADAS-to-brake/VCM network topology, sender identification for 0x2B0/0x1C3, and a concrete isolated bench connection design or verified same-network protocol. More ordinary driving logs or another synthetic handover test do not resolve this physical transport question.

No live vehicle commands, diagnostic disable, safety changes, or harness modifications were performed during this investigation.
