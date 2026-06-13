#include <stdio.h>
#include <string.h>
#include "nic_ksf.h"

static void print_hex(const char *label, const uint8_t *data, uint8_t len) {
    printf("%s: ", label);
    for (uint8_t i = 0; i < len; i++) printf("%02X ", data[i]);
    printf("\n");
}

int main(void) {
    printf("=== NIC-KSF core test ===\n\n");

    uint8_t key[16] = {0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,
                       0x09,0x0A,0x0B,0x0C,0x0D,0x0E,0x0F,0x10};

    /* Test 1: 16B symmetry */
    uint8_t d16[16] = "Hello, KSF!     ";
    uint8_t o16[16]; memcpy(o16, d16, 16);
    print_hex("Input ", d16, 16);
    ksf_encrypt(key, d16, 16);
    print_hex("Cipher", d16, 16);
    ksf_decrypt(key, d16, 16);
    print_hex("Output", d16, 16);
    printf("16B symmetry: %s\n\n", memcmp(d16, o16, 16)==0 ? "OK" : "FAIL");

    /* Test 2: various lengths */
    uint8_t ok = 1;
    for (uint8_t n = 1; n <= 64; n++) {
        uint8_t buf[64], orig[64];
        for (uint8_t i = 0; i < n; i++) buf[i] = i;
        memcpy(orig, buf, n);
        ksf_encrypt(key, buf, n);
        ksf_decrypt(key, buf, n);
        if (memcmp(buf, orig, n) != 0) { ok = 0; printf("FAIL at %dB\n", n); }
    }
    printf("Lengths 1-64B: %s\n\n", ok ? "OK" : "FAIL");

    /* Test 3: different keys give different ciphertext */
    uint8_t key2[16] = {0xFF,0xFE,0xFD,0xFC,0xFB,0xFA,0xF9,0xF8,
                        0xF7,0xF6,0xF5,0xF4,0xF3,0xF2,0xF1,0xF0};
    uint8_t data[16] = "Hello, KSF!     ";
    uint8_t c1[16], c2[16];
    memcpy(c1, data, 16); ksf_encrypt(key,  c1, 16);
    memcpy(c2, data, 16); ksf_encrypt(key2, c2, 16);
    printf("Different keys -> different ciphertext: %s\n\n",
           memcmp(c1, c2, 16) != 0 ? "OK" : "FAIL");

    printf("=== Done ===\n");
    return 0;
}
