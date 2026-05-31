/*
 * atmega_sd_writeonly.ino  —  NIC-MLA write-only on Arduino with an SD card
 *
 * Demonstrates the target scenario: the MCU only WRITES into 1MB containers and,
 * when one fills up, rolls over to the next itself (MLA00000.MLA, MLA00001.MLA, …).
 * Reading and aggregation across files is done later on a PC (Python MlaArchive) —
 * "the PC's CPU chews through everything".
 *
 * Free space = 0xFF (no superblock); pre-allocating 1 MB is a one-time, fast step.
 *
 * Dependencies: SdFat (https://github.com/greiman/SdFat)
 * Copy into the sketch folder: nic_mla_format.h, nic_mla_write.h, nic_mla_write.c
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#include <SdFat.h>
extern "C" {
  #include "nic_mla_write.h"
}

#define MLA_SIZE   (1UL << 20)   /* 1 MB per file */
#define SD_CS_PIN  10

static SdFat        sd;
static SdFile       g_file;
static mla_writer_t g_w;
static uint32_t     g_seq = 0;   /* sequence number of the current file */
static char         g_name[16];

/* ── HAL: binding to SdFat ───────────────────────────────────────────────── */
static int hal_read(void *ctx, uint32_t off, void *buf, uint16_t n) {
  (void)ctx; if (!g_file.seekSet(off)) return -1;
  return g_file.read(buf, n) == (int)n ? 0 : -1;
}
static int hal_write(void *ctx, uint32_t off, const void *buf, uint16_t n) {
  (void)ctx; if (!g_file.seekSet(off)) return -1;
  return g_file.write(buf, n) == (int)n ? 0 : -1;
}
static void     hal_sync(void *ctx) { (void)ctx; g_file.sync(); }
static uint32_t hal_size(void *ctx) { (void)ctx; return MLA_SIZE; }
static mla_hal_t make_hal() {
  mla_hal_t h; h.read = hal_read; h.write = hal_write;
  h.sync = hal_sync; h.size = hal_size; h.ctx = 0; return h;
}

static void name_for(uint32_t seq) { snprintf(g_name, sizeof(g_name), "MLA%05lu.MLA", (unsigned long)seq); }

/* Find the highest existing sequence number (0 if none). */
static bool find_latest(uint32_t *out_seq, bool *any) {
  uint32_t s = 0; *any = false;
  for (;;) { name_for(s); if (!sd.exists(g_name)) break; *any = true; *out_seq = s; s++; }
  return true;
}

/* Pre-allocate a new 1MB file (0xFF) and format it. */
static bool create_and_format(uint32_t seq) {
  name_for(seq);
  if (!g_file.open(g_name, O_RDWR | O_CREAT | O_TRUNC)) return false;
  uint8_t ff[64]; memset(ff, 0xFF, sizeof(ff));
  for (uint32_t i = 0; i < MLA_SIZE; i += sizeof(ff)) g_file.write(ff, sizeof(ff));
  g_file.sync();
  g_seq = seq;
  return mla_w_format(&g_w, make_hal(), MLA_SIZE, MLA_CRC_FULL, 12, 8) == MLA_OK;
}

static bool roll_to_next() {              /* current is full → next file */
  g_file.close();
  return create_and_format(g_seq + 1);
}

void setup() {
  Serial.begin(9600);
  if (!sd.begin(SD_CS_PIN)) { Serial.println(F("SD fail")); return; }

  uint32_t latest = 0; bool any = false;
  find_latest(&latest, &any);
  if (any) {                               /* continue in the last file */
    name_for(latest);
    g_file.open(g_name, O_RDWR);
    g_seq = latest;
    mla_w_mount(&g_w, make_hal());
  } else {                                 /* first run */
    create_and_format(0);
  }
  Serial.print(F("MLA ready: ")); Serial.print(g_name);
  Serial.print(F("  records=")); Serial.println(g_w.count);
}

void loop() {
  uint32_t ts = /* RTC unix time */ millis() / 1000;
  uint8_t  sample[4];
  int t = analogRead(A0);
  sample[0] = (uint8_t)t; sample[1] = (uint8_t)(t >> 8);
  sample[2] = (uint8_t)analogRead(A1); sample[3] = 0;

  int rc = mla_w_append(&g_w, ts, 1, sample, sizeof(sample), MLA_ENC_RAW, 0);
  if (rc == MLA_E_FULL) {                  /* file full → roll over */
    if (roll_to_next()) {
      Serial.print(F("rolled to ")); Serial.println(g_name);
      mla_w_append(&g_w, ts, 1, sample, sizeof(sample), MLA_ENC_RAW, 0);
    } else {
      Serial.println(F("roll failed"));
    }
  } else if (rc != MLA_OK) {
    Serial.println(F("append error"));
  }

  delay(15UL * 60UL * 1000UL);             /* 15 minutes */
}
