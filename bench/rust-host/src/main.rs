use std::time::Instant;
use wasmtime::component::{Component, Linker};
use wasmtime::{Config, Engine, Store};
use wasmtime_wasi::{ResourceTable, WasiCtx, WasiCtxBuilder, WasiCtxView, WasiView};

wasmtime::component::bindgen!({
    world: "codec",
    path: "../../wit/codec.wit",
});

struct State {
    ctx: WasiCtx,
    table: ResourceTable,
}

impl WasiView for State {
    fn ctx(&mut self) -> WasiCtxView<'_> {
        WasiCtxView {
            ctx: &mut self.ctx,
            table: &mut self.table,
        }
    }
}

fn main() -> anyhow::Result<()> {
    let mut config = Config::new();
    config.wasm_component_model(true);
    let engine = Engine::new(&config)?;

    let wasm_path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../codec/identity-c/identity.wasm");
    let component = Component::from_file(&engine, &wasm_path)?;

    let mut linker: Linker<State> = Linker::new(&engine);
    wasmtime_wasi::p2::add_to_linker_sync(&mut linker)?;

    let state = State {
        ctx: WasiCtxBuilder::new().build(),
        table: ResourceTable::new(),
    };
    let mut store = Store::new(&engine, state);
    let codec = Codec::instantiate(&mut store, &component, &linker)?;

    // Warm up the compilation cache (same as Python test conditions).
    // NOTE: the exact accessor name depends on wasmtime 41 bindgen output for
    // a world that re-exports a foreign interface. If this does not compile,
    // run `cargo build` and read the error — it will name the actual accessor.
    {
        let warm = vec![("bytes".to_string(), vec![0u8; 64])];
        let _ = codec.chonkle_codec_transform().call_decode(&mut store, &warm)?;
    }

    for (size, label) in [(1usize << 20, "1 MB"), (2usize << 20, "2 MB")] {
        let inputs = vec![("bytes".to_string(), vec![0u8; size])];
        for run in 1..=3 {
            let t0 = Instant::now();
            let result = codec
                .chonkle_codec_transform()
                .call_decode(&mut store, &inputs)?
                .map_err(|e| anyhow::anyhow!(e))?;
            let elapsed = t0.elapsed();
            let out = result.iter().map(|(_, v)| v.len()).sum::<usize>();
            let total = size + out;
            let throughput = total as f64 / elapsed.as_secs_f64() / 1_048_576.0;
            println!(
                "[TIMING] identity.wasm decode: fn={:.3}s  in={}B out={}B \
                 abi_total={}B throughput={:.1}MB/s  ({} run {}/3, Rust host)",
                elapsed.as_secs_f64(),
                size,
                out,
                total,
                throughput,
                label,
                run
            );
        }
    }
    Ok(())
}
