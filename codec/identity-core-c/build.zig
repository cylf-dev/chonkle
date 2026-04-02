const std = @import("std");

pub fn build(b: *std.Build) void {
    // Identity-core codec: core wasm module (no Component Model lift).

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
    compile.addFileArg(b.path("../shared/core_abi.c"));
    compile.addFileArg(b.path("codec_impl.c"));

    b.getInstallStep().dependOn(
        &b.addInstallFile(core_wasm, "identity-core.wasm").step,
    );
}
