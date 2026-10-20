//! Serve the `search_symbols` spike from a `.kg` path: `cargo run --release -p
//! cairn-daemon --example serve_search -- <path-to-store.kg>`.

use std::path::PathBuf;

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let Some(store) = std::env::args().nth(1) else {
        eprintln!("usage: serve_search <path-to-store.kg>");
        std::process::exit(2);
    };
    let (addr, handle) = cairn_daemon::server::serve(PathBuf::from(store)).await?;
    println!("serving search_symbols on http://{addr}/mcp");
    tokio::signal::ctrl_c().await?;
    handle.stop();
    Ok(())
}
