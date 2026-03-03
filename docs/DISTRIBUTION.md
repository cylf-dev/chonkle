# Remote Storage Options for .wasm Files

This document compares approaches for making `.wasm` build artifacts publicly available for download.

## GitHub Releases

Releases let you attach binary assets to tagged versions of a repository.

### How it works

1. Tag a version in the repo (e.g., `v0.1.0`)
2. CI builds the `.wasm` file
3. CI creates a release and attaches the `.wasm` as an asset using `gh release create` or the `softprops/action-gh-release` action

Download URL follows a predictable pattern:

```text
https://github.com/{owner}/{repo}/releases/download/{tag}/file.wasm
```

The latest release can be queried via the GitHub API.

### Pros

- Simple HTTP GET to download — no special tooling needed on the consumer side
- Trivial CI setup
- No authentication required for public repos
- Versioned naturally via git tags
- 2 GB per file limit

### Cons

- Not a registry — no standardized discovery, search, or metadata beyond what GitHub provides
- No concept of namespaces, media types, or multi-artifact manifests
- Consumers must know the exact repo and tag to fetch from

## GitHub Packages (language-specific registries)

GitHub Packages is an umbrella that hosts several package registries: npm, Maven, NuGet, and a Docker/OCI registry. The Docker/OCI registry is **GitHub Container Registry (GHCR)**, which is covered separately below because it works fundamentally differently — it supports arbitrary binary artifacts and anonymous public access. This section covers the language-specific registries only.

### How it works

The language-specific registries have no generic binary support. To use them for `.wasm` files, you would need to wrap the artifact in a supported package format — for example, publishing an npm package that contains the `.wasm` file, which consumers would install via npm/yarn.

### Pros

- Integrates with existing package manager workflows if consumers already use npm, Maven, etc.
- Scoped to the repository that published it
- Free for public repos

### Cons

- No native support for generic binary artifacts — you must shoehorn `.wasm` into a package format
- npm packages require `npm install` to consume, adding a Node.js dependency for non-JS consumers
- Most GitHub Packages registries require authentication even for public packages (Docker/GHCR is the exception)
- Adds unnecessary packaging overhead and complexity for a single `.wasm` file

## GitHub Container Registry (GHCR) with OCI Artifacts

GHCR (`ghcr.io`) is the OCI-compliant registry within GitHub Packages. While technically part of the same product, it behaves very differently from the language-specific registries above: it supports arbitrary OCI artifacts (not just Docker images), allows anonymous pulls for public packages, and is scoped to orgs/users rather than individual repos.

This is the approach the broader Wasm ecosystem is converging on.

### How it works

1. CI builds the `.wasm` file
2. Push the `.wasm` as an OCI artifact using a tool like [`oras`](https://oras.land/):

```bash
oras push ghcr.io/{owner}/{repo}:v0.1.0 codec.wasm:application/wasm
```

1. Consumers pull it with:

```bash
oras pull ghcr.io/{owner}/{repo}:v0.1.0
```

Or use an OCI client library in their language of choice.

### Pros

- OCI registries are becoming the standard distribution mechanism for Wasm modules (used by wasmCloud, Spin, Bytecode Alliance tooling, etc.)
- Supports rich metadata via OCI manifests (media types, annotations, multi-platform artifacts)
- Public packages on GHCR do not require authentication to pull
- Free for public repos
- Standardized protocol enables interoperability — artifacts could move to any OCI-compliant registry

### Cons

- More complex CI setup than Releases
- Consumers need an OCI client rather than a simple HTTP GET
- Python OCI client library ecosystem is less mature than HTTP libraries
- Less familiar to contributors who haven't worked with OCI outside of Docker

## Wasm Component Registry (warg / wa.dev)

The Bytecode Alliance has developed [warg](https://warg.io/), a registry protocol for Wasm packages, with [wa.dev](https://wa.dev/) as the public registry instance. The underlying transport is OCI, and the active tooling lives in the [`wasm-pkg-tools`](https://github.com/bytecodealliance/wasm-pkg-tools) project (the `wkg` CLI).

### Component Model orientation

warg and wa.dev are built around the [WebAssembly Component Model](https://component-model.bytecodealliance.org/), a higher-level abstraction on top of core WebAssembly. The registry expects artifacts to be Component Model components — a binary format distinct from plain wasm modules. Components declare typed interfaces using WIT (WebAssembly Interface Types), and the registry can index that metadata for discovery. Packages are addressed as `namespace:name@version` (a warg protocol convention), and even WIT interface definitions are compiled to wasm and published as packages.

Our `.wasm` codecs are **plain wasm modules** — they export functions with a specific ABI but don't use WIT or the Component Model. While the lower-level `wkg oci` commands (which are just OCI pushes) could technically be used, the registry's discovery, metadata, and tooling wouldn't apply. We'd be working against the grain of the ecosystem.

### Pros

- Purpose-built for Wasm distribution — the most "correct" long-term home for Wasm artifacts
- Federated and decentralized by design — multiple registries can be linked
- Rich metadata, namespacing, and type-safe interoperability for components
- Built on OCI, so the underlying transport is standardized

### Cons

- Oriented toward Component Model artifacts, not plain wasm modules
- Still maturing — the original warg registry implementation is no longer actively developed, with work continuing in `wasm-pkg-tools`
- Tooling and ecosystem are early-stage
- Would require adopting the Component Model to fully benefit, which is a significant architectural change

## Comparison

| | Releases | Packages | GHCR (OCI) | warg / wa.dev |
| --- | --- | --- | --- | --- |
| Setup complexity | Low | Medium | Medium | High |
| Consumer complexity | Low (HTTP GET) | Medium (package manager) | Medium (OCI client) | Medium (wkg CLI) |
| Auth required (public) | No | Yes (most registries) | No | No |
| Generic binary support | Yes | No (must wrap in package format) | Yes (OCI artifacts) | No (Component Model) |
| Discovery / metadata | Minimal | Package manager conventions | OCI manifests, annotations | Rich (Component Model interfaces, namespaces) |
| Ecosystem alignment | Generic | Language-specific | Wasm-native direction | Wasm-native (Component Model) |
| Versioning | Git tags | Package versions | OCI tags / digests | Semantic (namespace:name@ver) |
| Maturity | Stable | Stable | Stable | Early-stage |

## Current approach

Codec repositories use a single GitHub Actions workflow that publishes `.wasm` files to both **GitHub Releases** (as release assets) and **GHCR** (as OCI artifacts). This provides two consumption paths:

- **GitHub Releases** — simple HTTP GET, no tooling required on the consumer side
- **GHCR** — OCI-based distribution, aligned with where the Wasm ecosystem is heading

**warg / wa.dev** would become relevant if the project adopts the Component Model for its codec interface. Until then, its Component Model orientation makes it a poor fit for distributing plain wasm modules.

**GitHub Packages** (npm, Maven, etc.) is not a good fit for this use case and can be ruled out.
