#[allow(warnings)]
mod bindings;

use bindings::exports::chonkle::codec::transform::{Guest, PortMap};
use flate2::read::ZlibDecoder;
use flate2::write::ZlibEncoder;
use flate2::Compression;
use std::io::{Read, Write};

struct Component;

fn find_port<'a>(inputs: &'a PortMap, name: &str) -> Option<&'a Vec<u8>> {
    inputs.iter().find(|(k, _)| k == name).map(|(_, v)| v)
}

impl Guest for Component {
    fn encode(inputs: PortMap) -> Result<PortMap, String> {
        let bytes = find_port(&inputs, "bytes")
            .ok_or_else(|| "missing port: bytes".to_string())?;

        let level = find_port(&inputs, "level")
            .and_then(|v| std::str::from_utf8(v).ok())
            .and_then(|s| s.trim().parse::<u32>().ok())
            .unwrap_or(6)
            .min(9);

        let mut enc = ZlibEncoder::new(Vec::new(), Compression::new(level));
        enc.write_all(bytes).map_err(|e| e.to_string())?;
        let compressed = enc.finish().map_err(|e| e.to_string())?;

        Ok(vec![("bytes".to_string(), compressed)])
    }

    fn decode(inputs: PortMap) -> Result<PortMap, String> {
        let bytes = find_port(&inputs, "bytes")
            .ok_or_else(|| "missing port: bytes".to_string())?;

        let mut dec = ZlibDecoder::new(bytes.as_slice());
        let mut out = Vec::new();
        dec.read_to_end(&mut out).map_err(|e| e.to_string())?;

        Ok(vec![("bytes".to_string(), out)])
    }
}

bindings::export!(Component with_types_in bindings);
