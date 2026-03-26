/**
 * Identity codec using the core ABI (no Component Model).
 *
 * Implements the chonkle core ABI (alloc, dealloc, encode, decode)
 * with the binary port-map wire format. Both encode and decode
 * perform a memcpy, passing bytes through unchanged.
 *
 * Expected port-map inputs:
 *   "bytes"  raw data (required)
 *
 * Output port-map:
 *   "bytes"  same bytes as input
 */

#include "../shared/core_abi.h"
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

__attribute__((__export_name__("alloc")))
int32_t codec_alloc(int32_t size) {
    void *ptr = malloc((size_t)size);
    return (int32_t)(uintptr_t)ptr;
}

__attribute__((__export_name__("dealloc")))
void codec_dealloc(int32_t ptr, int32_t size) {
    (void)size;
    free((void *)(uintptr_t)ptr);
}

static int64_t transform(int32_t pm_ptr, int32_t pm_len) {
    uint8_t *input_buf = (uint8_t *)(uintptr_t)pm_ptr;
    core_abi_port_map_t pm = core_abi_parse_port_map(input_buf, (uint32_t)pm_len);

    if (pm.count == 0) {
        free(input_buf);
        return CORE_ABI_ERROR;
    }

    const core_abi_port_t *bytes_port = core_abi_find_port(&pm, "bytes");
    if (!bytes_port) {
        core_abi_free_port_map(&pm);
        free(input_buf);
        return CORE_ABI_ERROR;
    }

    /* Copy input bytes before freeing the input buffer. */
    uint32_t out_data_len = bytes_port->data_len;
    uint8_t *out_data = malloc(out_data_len);
    if (!out_data) {
        core_abi_free_port_map(&pm);
        free(input_buf);
        return CORE_ABI_ERROR;
    }
    memcpy(out_data, bytes_port->data, out_data_len);

    core_abi_free_port_map(&pm);
    free(input_buf);

    /* Build output port-map with a single "bytes" port. */
    core_abi_port_t out_entry;
    out_entry.name = "bytes";
    out_entry.name_len = 5;
    out_entry.data = out_data;
    out_entry.data_len = out_data_len;

    core_abi_port_map_t out_pm;
    out_pm.entries = &out_entry;
    out_pm.count = 1;

    uint32_t ser_len;
    uint8_t *ser_buf = core_abi_serialize_port_map(&out_pm, &ser_len);
    free(out_data);

    if (!ser_buf) return CORE_ABI_ERROR;

    return core_abi_pack_result((uint32_t)(uintptr_t)ser_buf, ser_len);
}

__attribute__((__export_name__("encode")))
int64_t encode(int32_t pm_ptr, int32_t pm_len) {
    return transform(pm_ptr, pm_len);
}

__attribute__((__export_name__("decode")))
int64_t decode(int32_t pm_ptr, int32_t pm_len) {
    return transform(pm_ptr, pm_len);
}
