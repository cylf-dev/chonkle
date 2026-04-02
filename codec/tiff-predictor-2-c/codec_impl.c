/**
 * WIT entry points for the tiff-predictor-2 Component Model codec.
 *
 * Implements chonkle:codec/transform@0.1.0 — encode applies horizontal
 * differencing, decode undoes it via cumulative sum.
 *
 * Expected port-map inputs:
 *   "bytes"            list<u8>  raw pixel data (required)
 *   "bytes_per_sample" list<u8>  UTF-8 integer: 1, 2, or 4 (required)
 *   "width"            list<u8>  UTF-8 integer: samples per row (required)
 *
 * Output port-map:
 *   "bytes"  list<u8>  transformed pixel data (same size as input)
 */

#include "../shared/codec.h"
#include <stdlib.h>
#include <string.h>

/* Forward declarations from tiff_predictor_2.c */
void diff_rows(uint8_t *buf, int width, int height, int bps);
void cumsum_rows(uint8_t *buf, int width, int height, int bps);

/*
 * Override the weak cabi_realloc from codec.c so that OOM calls
 * __builtin_trap() instead of abort(), avoiding WASI stderr imports.
 */
__attribute__((__export_name__("cabi_realloc")))
void *cabi_realloc(void *ptr, size_t old_size, size_t align, size_t new_size) {
    (void)old_size;
    if (new_size == 0) return (void *)align;
    void *ret = realloc(ptr, new_size);
    if (!ret) __builtin_trap();
    return ret;
}

/* Find a named port in the input port-map; returns NULL if absent. */
static codec_list_u8_t *find_port(
    exports_chonkle_codec_transform_port_map_t *inputs,
    const char *name)
{
    size_t name_len = strlen(name);
    for (size_t i = 0; i < inputs->len; i++) {
        codec_tuple2_port_name_list_u8_t *e = &inputs->ptr[i];
        if (e->f0.len == name_len && memcmp(e->f0.ptr, name, name_len) == 0)
            return &e->f1;
    }
    return NULL;
}

/*
 * Parse an unsigned integer from UTF-8 bytes (e.g. JSON constant "2" or "256").
 * Ignores non-digit bytes; returns 0 for empty input.
 */
static int parse_int(const uint8_t *data, size_t len) {
    int result = 0;
    for (size_t i = 0; i < len; i++) {
        if (data[i] >= '0' && data[i] <= '9')
            result = result * 10 + (data[i] - '0');
    }
    return result;
}

/*
 * Allocate a port-map with a single "bytes" port pointing to buf/buf_len.
 * Ownership of buf transfers to the caller's port-map.
 */
static bool make_bytes_result(
    uint8_t *buf, size_t buf_len,
    exports_chonkle_codec_transform_port_map_t *ret,
    codec_string_t *err)
{
    codec_tuple2_port_name_list_u8_t *entry = malloc(sizeof(*entry));
    if (!entry) {
        codec_string_set(err, "out of memory");
        return false;
    }
    codec_string_dup(&entry->f0, "bytes");
    entry->f1.ptr = buf;
    entry->f1.len = buf_len;
    ret->ptr = entry;
    ret->len = 1;
    return true;
}

static bool transform(
    exports_chonkle_codec_transform_port_map_t *inputs,
    exports_chonkle_codec_transform_port_map_t *ret,
    codec_string_t *err,
    int is_encode)
{
    codec_list_u8_t *bytes_port = find_port(inputs, "bytes");
    codec_list_u8_t *bps_port   = find_port(inputs, "bytes_per_sample");
    codec_list_u8_t *width_port = find_port(inputs, "width");

    if (!bytes_port) { codec_string_set(err, "missing port: bytes");            return false; }
    if (!bps_port)   { codec_string_set(err, "missing port: bytes_per_sample"); return false; }
    if (!width_port) { codec_string_set(err, "missing port: width");            return false; }

    int bps   = parse_int(bps_port->ptr,   bps_port->len);
    int width = parse_int(width_port->ptr, width_port->len);

    if (bps <= 0 || width <= 0) {
        codec_string_set(err, "bytes_per_sample and width must be positive");
        return false;
    }

    size_t input_len = bytes_port->len;
    int height = (int)(input_len / (size_t)(width * bps));

    uint8_t *out = malloc(input_len);
    if (!out) { codec_string_set(err, "out of memory"); return false; }
    memcpy(out, bytes_port->ptr, input_len);

    if (is_encode)
        diff_rows(out, width, height, bps);
    else
        cumsum_rows(out, width, height, bps);

    return make_bytes_result(out, input_len, ret, err);
}

bool exports_chonkle_codec_transform_encode(
    exports_chonkle_codec_transform_port_map_t *inputs,
    exports_chonkle_codec_transform_port_map_t *ret,
    codec_string_t *err)
{
    return transform(inputs, ret, err, 1);
}

bool exports_chonkle_codec_transform_decode(
    exports_chonkle_codec_transform_port_map_t *inputs,
    exports_chonkle_codec_transform_port_map_t *ret,
    codec_string_t *err)
{
    return transform(inputs, ret, err, 0);
}
