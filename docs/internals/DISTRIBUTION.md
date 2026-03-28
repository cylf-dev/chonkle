# Codec Distribution Strategy

## Context

Chonkle produces three kinds of codec backends:

- **Component Model Wasm** — compiled to a Component Model component binary with a typed WIT interface
- **Core Wasm** — compiled to a plain wasm module using a binary port-map wire format with raw `alloc`/`dealloc`/`encode`/`decode` exports; ABI is incompatible with the Component Model
- **Native** — Python numcodecs wrappers; distributed as Python packages through standard Python packaging, not as Wasm artifacts

This document covers distribution of the two Wasm backends only.

## Decision

Publish Wasm codec artifacts to **GitHub Releases** (as release assets) and **GHCR** (as OCI artifacts) in parallel. Defer distribution via warg / wa.dev until the tooling matures.

## Options considered

### GitHub Releases

Attach `.wasm` files as assets to tagged releases via CI (`gh release create` or `softprops/action-gh-release`). Download URL follows a predictable pattern; no authentication required for public repos. Consumers fetch with a plain HTTP GET.

**Key trade-off:** zero consumer tooling requirement, but no registry semantics — no namespacing, media types, or manifest metadata. Not where the Wasm ecosystem is heading.

### GitHub Container Registry (GHCR) with OCI artifacts

Push `.wasm` files as OCI artifacts to `ghcr.io` using [`oras`](https://oras.land/). Anonymous pulls for public packages; supports rich metadata via OCI manifests. OCI is the distribution substrate the broader Wasm ecosystem is converging on — wasmCloud, Spin, and Bytecode Alliance tooling all use it.

**Key trade-off:** consumers need an OCI client rather than a plain HTTP GET, and the Python OCI client ecosystem is less mature. The complexity is real but bounded.

### warg / wa.dev

The Bytecode Alliance's [warg](https://warg.io/) registry protocol, with [wa.dev](https://wa.dev/) as the public instance. Built around the Component Model: artifacts are expected to be component binaries that declare typed WIT interfaces; packages are addressed as `namespace:name@version`. Built on OCI transport; higher-level registries like wa.dev build the Component Model protocol on top of it. Active tooling lives in [`wasm-pkg-tools`](https://github.com/bytecodealliance/wasm-pkg-tools) (`wkg` CLI).

**Key trade-off:** the most semantically correct home for Component Model codecs, but the original warg registry implementation is no longer actively developed and the tooling is early-stage. Core Wasm codecs are incompatible with the Component Model ABI and cannot be published here regardless of tooling maturity.

### GitHub Packages (language-specific registries)

npm, Maven, NuGet, etc. No native support for generic binary artifacts — distribution would require wrapping `.wasm` files in a language-specific package format. Not applicable.

## Rationale

The dual Releases + GHCR approach covers both consumption patterns that matter today:

- Releases provides a zero-friction path for consumers who just need to download a file
- GHCR aligns with the ecosystem trajectory and supports richer metadata

warg / wa.dev is the right long-term home for Component Model codecs specifically, but migrating to it requires both tooling maturity and a decision about how to handle Core Wasm codecs (which are ineligible). Until that is resolved, a format-agnostic mechanism is necessary anyway.

## Consequences

- CI must push to both Releases and GHCR on each tagged release
- Consumers using OCI need `oras` or an OCI client library
- When warg tooling matures, Component Model codecs can be additionally published to wa.dev; Core Wasm codecs will continue to use Releases or GHCR
- Native codecs are out of scope here — they follow standard Python packaging
