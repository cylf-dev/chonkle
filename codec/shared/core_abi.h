/*
 * Core ABI helpers for chonkle core wasm codecs.
 *
 * Provides port-map serialization/deserialization for the binary wire
 * format defined in docs/reference/codec-contract/core.md.
 */
#ifndef CORE_ABI_H
#define CORE_ABI_H

#include <stddef.h>
#include <stdint.h>

/* A single port entry in a port-map. */
typedef struct {
    const char *name;
    uint32_t name_len;
    const uint8_t *data;
    uint32_t data_len;
} core_abi_port_t;

/* A parsed port-map. */
typedef struct {
    core_abi_port_t *entries;
    uint32_t count;
} core_abi_port_map_t;

/*
 * Parse a serialized port-map from the wire format.
 *
 * The returned port-map's name/data pointers reference the input buffer
 * directly (zero-copy parse). The caller must not free the input buffer
 * while the port-map is in use. Free the entries array (not the data)
 * with core_abi_free_port_map().
 *
 * Returns a port-map with count=0 on parse error.
 */
core_abi_port_map_t core_abi_parse_port_map(const uint8_t *buf, uint32_t len);

/*
 * Serialize a port-map to the wire format.
 *
 * Returns a malloc'd buffer. Caller takes ownership.
 * Sets *out_len to the serialized length.
 * Returns NULL on allocation failure.
 */
uint8_t *core_abi_serialize_port_map(
    const core_abi_port_map_t *pm, uint32_t *out_len);

/*
 * Find a port by name. Returns NULL if not found.
 */
const core_abi_port_t *core_abi_find_port(
    const core_abi_port_map_t *pm, const char *name);

/*
 * Free a parsed port-map (frees the entries array, not the referenced data).
 */
void core_abi_free_port_map(core_abi_port_map_t *pm);

/* Pack a (ptr, len) pair into a single i64 return value. */
static inline int64_t core_abi_pack_result(uint32_t ptr, uint32_t len) {
    return ((int64_t)ptr << 32) | (int64_t)len;
}

/* Error return value: ptr=0, len=0. */
#define CORE_ABI_ERROR ((int64_t)0)

#endif /* CORE_ABI_H */
