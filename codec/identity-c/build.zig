const std = @import("std");

pub fn build(b: *std.Build) void {
    // Identity codec is a benchmarking artifact only — always compile at -O2.

    // Step 1: compile C sources to a core wasm32-wasi reactor module.
    const compile = b.addSystemCommand(&.{
        b.graph.zig_exe,
        "cc",
        "--target=wasm32-wasi",
        "-mexec-model=reactor",
        "-fvisibility=hidden",
        "-lc",
        "-O2",
    });

    compile.addArg("-o");
    const core_wasm = compile.addOutputFileArg("identity-core.wasm");
    compile.addFileArg(b.path("../shared/codec.c"));
    compile.addFileArg(b.path("codec_impl.c"));
    compile.addFileArg(b.path("../shared/codec_component_type.o"));

    // Step 2: lift the core module to a Component Model .wasm via the WASI
    // preview1 → preview2 adapter bundled in codec/wasi_snapshot_preview1.reactor.wasm.
    const componentize = b.addSystemCommand(&.{ "wasm-tools", "component", "new" });
    componentize.addFileArg(core_wasm);
    componentize.addArg("--adapt");
    componentize.addPrefixedFileArg(
        "wasi_snapshot_preview1=",
        b.path("../wasi_snapshot_preview1.reactor.wasm"),
    );
    componentize.addArg("-o");
    const component = componentize.addOutputFileArg("identity.wasm");

    b.getInstallStep().dependOn(
        &b.addInstallFile(component, "identity.wasm").step,
    );
}
