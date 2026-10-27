#pragma once

#include "opendbc/safety/declarations.h"

#define NISSAN_COMMON_RX_CHECKS                                                                                                                           \
  {.msg = {{0x185, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},  /* STEER_TORQUE_SENSOR */  \
  {.msg = {{0x285, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},                                              \
           {0x285, 1, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }}}, /* WHEEL_SPEEDS_REAR */             \
  {.msg = {{0x30f, 2, 3, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},                                              \
           {0x30f, 1, 3, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }}}, /* CRUISE_STATE */                  \
  {.msg = {{0x15c, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},                                              \
           {0x15c, 1, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},                                              \
           {0x239, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}}}, /* GAS_PEDAL */                            \
  {.msg = {{0x454, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},                                              \
           {0x454, 1, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},                                              \
           {0x1cc, 0, 4, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}}}, /* DOORS_LIGHTS / BRAKE */                \

#define NISSAN_PRO_PILOT_RX_CHECKS(alt_eps_bus)                                                                                                      \
  {.msg = {{0x1B6, alt_eps_bus, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \

static bool nissan_alt_eps = false;
static bool nissan_auto_resume = false;
static bool nissan_leaf_stock_res = false;
static bool nissan_ppp_enabled = false;
static bool nissan_ppp_seen = false;
static bool nissan_ppp_active = false;
static bool nissan_ppp_rearm = false;
static bool nissan_ppp_main_off_seen = false;
static bool nissan_ppp_main_on = false;
static bool nissan_ppp_cruise_on = false;
static bool nissan_ppp_counter_seen = false;
static uint8_t nissan_ppp_counter = 0U;
static uint32_t nissan_ppp_ts = 0U;

static bool nissan_ppp_fresh(void) {
  return nissan_ppp_seen && (safety_get_ts_elapsed(microsecond_timer_get(), nissan_ppp_ts) <= 100000U);
}

static void nissan_ppp_fault(void) {
  nissan_ppp_rearm = true;
  nissan_ppp_main_off_seen = false;
}

static bool nissan_ppp_controls_blocked(void) {
  if (nissan_ppp_enabled && !nissan_ppp_fresh() && nissan_ppp_seen) {
    nissan_ppp_fault();
  }
  return nissan_ppp_enabled && (!nissan_ppp_fresh() || nissan_ppp_active || nissan_ppp_rearm);
}

static bool nissan_ppp_checksum_valid(const CANPacket_t *msg) {
  if (GET_LEN(msg) != 8) {
    return false;
  }
  uint8_t crc = 0xFFU;  // mk_crc8_fun(init_crc=0, xor_out=0xFF)
  for (int i = 0; i < 7; i++) {
    crc ^= msg->data[i];
    for (int j = 0; j < 8; j++) {
      crc = ((crc & 0x80U) != 0U) ? (uint8_t)((crc << 1) ^ 0x1DU) : (uint8_t)(crc << 1);
    }
  }
  return (uint8_t)(crc ^ 0xFFU) == msg->data[7];
}

static void nissan_ppp_rx(const CANPacket_t *msg) {
  (void)nissan_ppp_controls_blocked();  // latch a timeout before accepting a recovered frame
  if ((msg->addr == 0x169U) && (msg->bus == 2U)) {
    if (!nissan_ppp_checksum_valid(msg)) {
      nissan_ppp_seen = false;
      nissan_ppp_fault();
    } else {
      uint8_t counter = msg->data[6] & 0xFU;
      uint8_t delta = (counter - nissan_ppp_counter) & 0xFU;
      bool counter_ok = !nissan_ppp_counter_seen || ((delta >= 1U) && (delta <= 3U));
      nissan_ppp_counter = counter;
      nissan_ppp_counter_seen = true;
      if (!counter_ok) {
        nissan_ppp_seen = false;
        nissan_ppp_fault();
      } else {
        nissan_ppp_seen = true;
        nissan_ppp_ts = microsecond_timer_get();
        nissan_ppp_active = GET_BIT(msg, 53U);
        if (nissan_ppp_active) {
          nissan_ppp_fault();
        }
      }
    }
  }
  if ((msg->addr == 0x30FU) && (msg->bus == 2U)) {
    nissan_ppp_cruise_on = GET_BIT(msg, 3U);
  }
  if ((msg->addr == 0x239U) && (msg->bus == 0U)) {
    bool main_on = GET_BIT(msg, 17U);
    if (nissan_ppp_fresh() && !nissan_ppp_active && !nissan_ppp_cruise_on) {
      if (!main_on) {
        nissan_ppp_main_off_seen = true;
      } else if (!nissan_ppp_main_on && nissan_ppp_main_off_seen) {
        nissan_ppp_rearm = false;
        nissan_ppp_main_off_seen = false;
      }
    }
    nissan_ppp_main_on = main_on;
  }
}

static bool nissan_fwd_hook(int bus_num, int addr) {
  bool replacement = ((bus_num == 2) && ((addr == 0x169) || (addr == 0x2B1) || (addr == 0x4CC))) ||
                     ((bus_num == 0) && (addr == 0x239));
  if (!replacement) {
    return false;
  }
  // Only the experimental Leaf config opts these messages out of static blocking.
  if (!nissan_ppp_enabled) {
    return (bus_num == 2);
  }
  bool blocked = nissan_ppp_controls_blocked();
  if (addr == 0x169) {
    return !(nissan_ppp_fresh() && nissan_ppp_active);
  }
  return !blocked;  // while host is inhibited, preserve stock HUD and driver buttons
}

static bool nissan_ppp_fwd_packet_blocked(const CANPacket_t *msg) {
  if (!nissan_ppp_enabled || (msg->bus != 2U) || (msg->addr != 0x169U)) {
    return false;
  }
  uint8_t delta = ((msg->data[6] & 0xFU) - nissan_ppp_counter) & 0xFU;
  return !nissan_ppp_checksum_valid(msg) || !GET_BIT(msg, 53U) || (delta < 1U) || (delta > 3U);
}

static const AngleSteeringLimits NISSAN_STEERING_LIMITS = {
  .max_angle = 60000,  // 600 deg, reasonable limit
  .angle_deg_to_can = 100,
  .angle_rate_up_lookup = {
    {0., 5., 15.},
    {5., .8, .15}
  },
  .angle_rate_down_lookup = {
    {0., 5., 15.},
    {5., 3.5, .4}
  },
};

static void nissan_rx_hook(const CANPacket_t *msg) {
  if (nissan_ppp_enabled) {
    nissan_ppp_rx(msg);
  }

  // Altima: on camera bus, others: on pt bus
  if (msg->bus == 0U) {
    if (msg->addr == 0x185U) {
      // Current steering angle
      int angle_meas_new = (msg->data[2] << 10) | (msg->data[3] << 2) | (msg->data[4] >> 6);
      // Factor is -0.01, offset is 1310. Flip to correct sign, but keep units in CAN scale
      angle_meas_new = -angle_meas_new + (1310.0f * NISSAN_STEERING_LIMITS.angle_deg_to_can);

      // update array of samples
      update_sample(&angle_meas, angle_meas_new);
    }
  }

  if (msg->bus == (nissan_alt_eps ? 1U : 0U)) {
    if (msg->addr == 0x285U) {
      // Get current speed and standstill
      uint16_t right_rear = (msg->data[0] << 8) | (msg->data[1]);
      uint16_t left_rear = (msg->data[2] << 8) | (msg->data[3]);
      vehicle_moving = (right_rear | left_rear) != 0U;
      UPDATE_VEHICLE_SPEED((right_rear + left_rear) / 2.0 * 0.005 * KPH_TO_MS);
    }

    // X-Trail 0x15c, Leaf 0x239
    if ((msg->addr == 0x15cU) || (msg->addr == 0x239U)) {
      if (msg->addr == 0x15cU){
        gas_pressed = ((msg->data[5] << 2) | ((msg->data[6] >> 6) & 0x3U)) > 3U;
      } else {
        gas_pressed = msg->data[0] > 3U;
      }
    }

    // X-trail 0x454, Leaf 0x239
    if ((msg->addr == 0x454U) || (msg->addr == 0x239U)) {
      if (msg->addr == 0x454U){
        brake_pressed = (msg->data[2] & 0x80U) != 0U;
      } else {
        brake_pressed = ((msg->data[4] >> 5) & 1U) != 0U;
      }
    }
  }

  // Handle cruise enabled
  if ((msg->addr == 0x30fU) && (msg->bus == (nissan_alt_eps ? 1U : 2U))) {
    bool cruise_engaged = (msg->data[0] >> 3) & 1U;
    pcm_cruise_check(cruise_engaged);
  }

  if ((msg->addr == 0x239U) && (msg->bus == 0U)) {
    acc_main_on = GET_BIT(msg, 17U);
    // Leaf CRUISE_THROTTLE.RES_BUTTON, bit 28. Remember the driver's button so a
    // forwarded press is still allowed when cruise is not engaged.
    nissan_leaf_stock_res = GET_BIT(msg, 28U);
  }

  if ((msg->addr == 0x1B6U) && (msg->bus == (nissan_alt_eps ? 2U : 1U))) {
    acc_main_on = GET_BIT(msg, 36U);
  }
}


static bool nissan_tx_hook(const CANPacket_t *msg) {
  bool tx = true;
  bool violation = false;

  // steer cmd checks
  if (msg->addr == 0x169U) {
    int desired_angle = ((msg->data[0] << 10) | (msg->data[1] << 2) | ((msg->data[2] >> 6) & 0x3U));
    bool lka_active = (msg->data[6] >> 4) & 1U;

    // Factor is -0.01, offset is 1310. Flip to correct sign, but keep units in CAN scale
    desired_angle = -desired_angle + (1310.0f * NISSAN_STEERING_LIMITS.angle_deg_to_can);

    if (steer_angle_cmd_checks(desired_angle, lka_active, NISSAN_STEERING_LIMITS)) {
      violation = true;
    }
  }

  // Leaf auto-resume injects RES on the relayed 0x239 frame. Allow that bit only
  // while stock cruise has enabled controls, unless this frame is echoing the
  // driver's own RES (captured on bus 0). Rejecting the frame is fail-closed:
  // the gas/brake copy in this message is dropped for that one cycle.
  if (nissan_auto_resume && (msg->addr == 0x239U) && GET_BIT(msg, 28U) &&
      !controls_allowed && !nissan_leaf_stock_res) {
    violation = true;
  }

  if (violation) {
    tx = false;
  }

  return tx;
}


static safety_config nissan_init(uint16_t param) {
  static const CanMsg NISSAN_TX_MSGS_LEAF[] = {
    {0x169, 0, 8, .check_relay = true},   // LKAS
    {0x2b1, 0, 8, .check_relay = true},   // PROPILOT_HUD
    {0x4cc, 0, 8, .check_relay = true},   // PROPILOT_HUD_INFO_MSG
    {0x239, 2, 8, .check_relay = true}    // CRUISE_THROTTLE (Leaf)
  };

  static const CanMsg NISSAN_TX_MSGS_LEAF_PPP[] = {
    {0x169, 0, 8, .check_relay = true, .disable_static_blocking = true},
    {0x2b1, 0, 8, .check_relay = true, .disable_static_blocking = true},
    {0x4cc, 0, 8, .check_relay = true, .disable_static_blocking = true},
    {0x239, 2, 8, .check_relay = true, .disable_static_blocking = true},
  };

  static const CanMsg NISSAN_TX_MSGS_XTRAIL[] = {
    {0x169, 0, 8, .check_relay = true},   // LKAS
    {0x2b1, 0, 8, .check_relay = true},   // PROPILOT_HUD
    {0x4cc, 0, 8, .check_relay = true},   // PROPILOT_HUD_INFO_MSG
    {0x20b, 2, 6, .check_relay = true}    // CRUISE_THROTTLE (X-Trail)
  };

  static const CanMsg NISSAN_TX_MSGS_ALTIMA[] = {
    {0x169, 0, 8, .check_relay = true},   // LKAS
    {0x2b1, 0, 8, .check_relay = true},   // PROPILOT_HUD
    {0x4cc, 0, 8, .check_relay = true},   // PROPILOT_HUD_INFO_MSG
    {0x20b, 1, 6, .check_relay = false}   // CRUISE_THROTTLE (Altima)
  };

  // Signals duplicated below due to the fact that these messages can come in on either CAN bus, depending on car model.
  static RxCheck nissan_rx_checks[] = {
    NISSAN_COMMON_RX_CHECKS
    NISSAN_PRO_PILOT_RX_CHECKS(1)
  };

  static RxCheck nissan_alt_eps_rx_checks[] = {
    NISSAN_COMMON_RX_CHECKS
    NISSAN_PRO_PILOT_RX_CHECKS(2)
  };

  static RxCheck nissan_leaf_rx_checks[] = {
    NISSAN_COMMON_RX_CHECKS
  };

  static RxCheck nissan_leaf_ppp_rx_checks[] = {
    NISSAN_COMMON_RX_CHECKS
    // CRC and rolling-counter validation in nissan_ppp_rx.
    {.msg = {{0x169, 2, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
  };

  // EPS Location. false = V-CAN, true = C-CAN
  const uint16_t NISSAN_PARAM_ALT_EPS_BUS = 1;

  const uint16_t NISSAN_PARAM_SP_LEAF = 1;
  const uint16_t NISSAN_PARAM_SP_PPP_INTERLOCK = 2;
  const uint16_t NISSAN_PARAM_SP_AUTO_RESUME = 4;

  nissan_alt_eps = GET_FLAG(param, NISSAN_PARAM_ALT_EPS_BUS);
  const bool nissan_leaf = GET_FLAG(current_safety_param_sp, NISSAN_PARAM_SP_LEAF);
  nissan_auto_resume = nissan_leaf && !nissan_alt_eps && GET_FLAG(current_safety_param_sp, NISSAN_PARAM_SP_AUTO_RESUME);
  nissan_leaf_stock_res = false;

  nissan_ppp_enabled = nissan_leaf && !nissan_alt_eps && GET_FLAG(current_safety_param_sp, NISSAN_PARAM_SP_PPP_INTERLOCK);
  nissan_ppp_seen = false;
  nissan_ppp_active = false;
  nissan_ppp_rearm = false;
  nissan_ppp_main_off_seen = false;
  nissan_ppp_main_on = false;
  nissan_ppp_cruise_on = false;
  nissan_ppp_counter_seen = false;
  nissan_ppp_counter = 0U;
  nissan_ppp_ts = 0U;

  safety_config ret;
  if (nissan_ppp_enabled) {
    SET_TX_MSGS(NISSAN_TX_MSGS_LEAF_PPP, ret);
    SET_RX_CHECKS(nissan_leaf_ppp_rx_checks, ret);
  } else if (nissan_leaf) {
    SET_TX_MSGS(NISSAN_TX_MSGS_LEAF, ret);
    SET_RX_CHECKS(nissan_leaf_rx_checks, ret);
  } else if (nissan_alt_eps) {
    SET_TX_MSGS(NISSAN_TX_MSGS_ALTIMA, ret);
    SET_RX_CHECKS(nissan_alt_eps_rx_checks, ret);
  } else {
    SET_TX_MSGS(NISSAN_TX_MSGS_XTRAIL, ret);
    SET_RX_CHECKS(nissan_rx_checks, ret);
  }

  return ret;
}

const safety_hooks nissan_hooks = {
  .init = nissan_init,
  .rx = nissan_rx_hook,
  .tx = nissan_tx_hook,
  .fwd = nissan_fwd_hook,
  .controls_blocked = nissan_ppp_controls_blocked,
  .fwd_packet_blocked = nissan_ppp_fwd_packet_blocked,
};
