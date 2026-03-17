const std = @import("std");

pub fn build(b: *std.Build) void {
    const optimize = b.standardOptimizeOption(.{});

    // Step 1: compile C sources to a core wasm32-wasi reactor module.
    const compile = b.addSystemCommand(&.{
        b.graph.zig_exe,
        "cc",
        "--target=wasm32-wasi",
        "-mexec-model=reactor",
        "-fvisibility=hidden",
        "-lc",
    });

    switch (optimize) {
        .Debug => compile.addArg("-Og"),
        .ReleaseSafe, .ReleaseFast => compile.addArg("-O2"),
        .ReleaseSmall => compile.addArg("-Os"),
    }

    compile.addArg("-o");
    const core_wasm = compile.addOutputFileArg("tiff-predictor-2-core.wasm");
    compile.addFileArg(b.path("../shared/codec.c"));
    compile.addFileArg(b.path("tiff_predictor_2.c"));
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
    const component = componentize.addOutputFileArg("tiff-predictor-2.wasm");

    b.getInstallStep().dependOn(
        &b.addInstallFile(component, "tiff-predictor-2.wasm").step,
    );
}
