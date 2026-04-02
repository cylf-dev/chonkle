/*
 * Core ABI helpers for chonkle core wasm codecs.
 *
 * See core_abi.h for API documentation.
 */
#include "core_abi.h"

#include <stdlib.h>
#include <string.h>

static uint32_t read_u32_le(const uint8_t *p) {
    return (uint32_t)p[0]
         | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

static void write_u32_le(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

core_abi_port_map_t core_abi_parse_port_map(const uint8_t *buf, uint32_t len) {
    core_abi_port_map_t pm = {NULL, 0};
    if (len < 4) return pm;

    uint32_t count = read_u32_le(buf);
    uint32_t offset = 4;

    if (count == 0) { pm.count = 0; return pm; }

    core_abi_port_t *entries = malloc(count * sizeof(core_abi_port_t));
    if (!entries) return pm;

    for (uint32_t i = 0; i < count; i++) {
        if (offset + 4 > len) goto fail;
        uint32_t name_len = read_u32_le(buf + offset);
        offset += 4;

        if (offset + name_len > len) goto fail;
        entries[i].name = (const char *)(buf + offset);
        entries[i].name_len = name_len;
        offset += name_len;

        if (offset + 4 > len) goto fail;
        uint32_t data_len = read_u32_le(buf + offset);
        offset += 4;

        if (offset + data_len > len) goto fail;
        entries[i].data = buf + offset;
        entries[i].data_len = data_len;
        offset += data_len;
    }

    pm.entries = entries;
    pm.count = count;
    return pm;

fail:
    free(entries);
    return (core_abi_port_map_t){NULL, 0};
}

uint8_t *core_abi_serialize_port_map(
    const core_abi_port_map_t *pm, uint32_t *out_len)
{
    uint32_t total = 4;
    for (uint32_t i = 0; i < pm->count; i++)
        total += 4 + pm->entries[i].name_len + 4 + pm->entries[i].data_len;

    uint8_t *buf = malloc(total);
    if (!buf) { *out_len = 0; return NULL; }

    uint32_t offset = 0;
    write_u32_le(buf + offset, pm->count);
    offset += 4;

    for (uint32_t i = 0; i < pm->count; i++) {
        write_u32_le(buf + offset, pm->entries[i].name_len);
        offset += 4;
        memcpy(buf + offset, pm->entries[i].name, pm->entries[i].name_len);
        offset += pm->entries[i].name_len;
        write_u32_le(buf + offset, pm->entries[i].data_len);
        offset += 4;
        memcpy(buf + offset, pm->entries[i].data, pm->entries[i].data_len);
        offset += pm->entries[i].data_len;
    }

    *out_len = total;
    return buf;
}

const core_abi_port_t *core_abi_find_port(
    const core_abi_port_map_t *pm, const char *name)
{
    uint32_t name_len = (uint32_t)strlen(name);
    for (uint32_t i = 0; i < pm->count; i++) {
        if (pm->entries[i].name_len == name_len &&
            memcmp(pm->entries[i].name, name, name_len) == 0)
            return &pm->entries[i];
    }
    return NULL;
}

void core_abi_free_port_map(core_abi_port_map_t *pm) {
    free(pm->entries);
    pm->entries = NULL;
    pm->count = 0;
}
