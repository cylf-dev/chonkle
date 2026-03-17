/**
 * TIFF horizontal differencing predictor (Predictor=2), core algorithm.
 *
 * encode: row-wise differencing (diff_rows)
 * decode: cumulative sum per row (cumsum_rows)
 *
 * Supports bytes-per-sample values of 1, 2, and 4.
 * Both functions operate in-place on a caller-provided buffer.
 */

#include <stdint.h>

/**
 * Undo horizontal differencing in-place: cumulative sum per row.
 * width is in samples; height is derived by the caller.
 */
void cumsum_rows(uint8_t *buf, int width, int height, int bps) {
    for (int row = 0; row < height; row++) {
        int base = row * width * bps;

        if (bps == 1) {
            uint8_t *s = buf + base;
            for (int col = 1; col < width; col++)
                s[col] += s[col - 1];
        } else if (bps == 2) {
            uint16_t *s = (uint16_t *)(buf + base);
            for (int col = 1; col < width; col++)
                s[col] += s[col - 1];
        } else if (bps == 4) {
            uint32_t *s = (uint32_t *)(buf + base);
            for (int col = 1; col < width; col++)
                s[col] += s[col - 1];
        }
    }
}

/**
 * Apply horizontal differencing in-place: row-wise differences.
 * Iterates backwards so each subtraction reads the original predecessor.
 */
void diff_rows(uint8_t *buf, int width, int height, int bps) {
    for (int row = 0; row < height; row++) {
        int base = row * width * bps;

        if (bps == 1) {
            uint8_t *s = buf + base;
            for (int col = width - 1; col > 0; col--)
                s[col] -= s[col - 1];
        } else if (bps == 2) {
            uint16_t *s = (uint16_t *)(buf + base);
            for (int col = width - 1; col > 0; col--)
                s[col] -= s[col - 1];
        } else if (bps == 4) {
            uint32_t *s = (uint32_t *)(buf + base);
            for (int col = width - 1; col > 0; col--)
                s[col] -= s[col - 1];
        }
    }
}
