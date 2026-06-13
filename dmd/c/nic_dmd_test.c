// SPDX-License-Identifier: MIT

#include "nic_dmd.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

int total_errors = 0;

void check(const char* name, bool ok) {
    if (!ok) {
        printf("  FAIL: %s\n", name);
        total_errors++;
    }
}

int main() {
    printf("\n=== NIC DMD - C tests ===\n\n");
    
    int pkt_lens[] = {8, 16, 32, 64, 128, 255};
    int num_lens = sizeof(pkt_lens) / sizeof(pkt_lens[0]);

    printf("Test 1: round-trip (pseudo-random data)\n");
    srand(42);

    for (int j = 0; j < num_lens; j++) {
        uint8_t pkt_len = pkt_lens[j];
        dmd_encoder_t enc;
        dmd_decoder_t dec;
        
        dmd_encoder_init(&enc, pkt_len);
        dmd_decoder_init(&dec, pkt_len);
        
        int errors = 0;
        for (int i = 0; i < 500; i++) {
            uint8_t data[DMD_ENC_BUF_SIZE];
            uint8_t comp[DMD_ENC_BUF_SIZE + 1];
            uint8_t decomp[DMD_ENC_BUF_SIZE];

            for (int k = 0; k < pkt_len; k++) {
                data[k] = rand() % 256;
            }

            uint16_t c_len = dmd_compress(&enc, data, comp);
            
            // Handle return value per the current spec (0 = success)
            int res = dmd_decompress(&dec, comp, c_len, decomp);
            if (res < 0) {
                errors++;
            } else if (memcmp(data, decomp, pkt_len) != 0) {
                errors++;
            }
        }
        
        printf("  pkt_len=%3d: 500 packets: %s\n", pkt_len, (errors == 0) ? "OK" : "FAIL");
        check("round-trip", errors == 0);
    }

    // Test 2: Verify the safety guard (reserved protocol version 7)
    printf("\nTest 2: Reserved protocol version (sample_num=7)\n");
    dmd_decoder_t dec_test;
    dmd_decoder_init(&dec_test, 16);
    
    uint8_t dummy_comp[16] = {0};
    dummy_comp[0] = 7; // Set header to sample_num = 7
    uint8_t dummy_decomp[16];
    
    int res = dmd_decompress(&dec_test, dummy_comp, 16, dummy_decomp);
    if (res == -3) {
        printf("  OK (error -3 correctly raised for unsupported version 7)\n");
    } else {
        printf("  FAIL: decoder wrongly accepted a packet with a reserved version!\n");
        check("reserved version", false);
    }

    printf("\nTests done. Total errors: %d\n\n", total_errors);
    return (total_errors == 0) ? 0 : 1;
}
