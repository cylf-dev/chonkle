/**
 * WIT entry points for the identity Component Model codec.
 *
 * Implements chonkle:codec/transform@0.1.0 — both encode and decode
 * perform a memcpy, passing bytes through unchanged. This isolates
 * pure ABI cost from codec computation for benchmarking purposes.
 *
 * Expected port-map inputs:
 *   "bytes"  list<u8>  raw data (required)
 *
 * Output port-map:
 *   "bytes"  list<u8>  same bytes as input
 */

#include "../shared/codec.h"
#include <stdlib.h>
#include <string.h>

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
    codec_string_t *err)
{
    codec_list_u8_t *bytes_port = find_port(inputs, "bytes");
    if (!bytes_port) { codec_string_set(err, "missing port: bytes"); return false; }

    size_t input_len = bytes_port->len;
    uint8_t *out = malloc(input_len);
    if (!out) { codec_string_set(err, "out of memory"); return false; }
    memcpy(out, bytes_port->ptr, input_len);

    return make_bytes_result(out, input_len, ret, err);
}

bool exports_chonkle_codec_transform_encode(
    exports_chonkle_codec_transform_port_map_t *inputs,
    exports_chonkle_codec_transform_port_map_t *ret,
    codec_string_t *err)
{
    return transform(inputs, ret, err);
}

bool exports_chonkle_codec_transform_decode(
    exports_chonkle_codec_transform_port_map_t *inputs,
    exports_chonkle_codec_transform_port_map_t *ret,
    codec_string_t *err)
{
    return transform(inputs, ret, err);
}
