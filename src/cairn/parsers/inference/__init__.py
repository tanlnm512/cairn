"""Post-pass inference helpers for query-extracted parser data.

This package previously hosted a Kotlin receiver-type post-pass, but that
logic now lives inline in :mod:`cairn.parsers.kotlin` (see its
``_infer_call_receiver_type``), so the standalone module was removed. The
package is kept as an extension point for future inference passes.
"""
