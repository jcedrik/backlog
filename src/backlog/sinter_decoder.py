"""sinter integration.

Use in Python:

    sinter.collect(..., decoders=["backlog-uf"],
                   custom_decoders=backlog.sinter_decoders())

or from the sinter command line:

    sinter collect ... --decoders backlog-uf \\
        --custom_decoders_module_function "backlog:sinter_decoders"
"""
from __future__ import annotations

import numpy as np
import sinter
import stim

from .decoder import UnionFindDecoder


class _CompiledUnionFind(sinter.CompiledDecoder):
    def __init__(self, decoder: UnionFindDecoder):
        self.decoder = decoder

    def decode_shots_bit_packed(self, *, bit_packed_detection_event_data: np.ndarray) -> np.ndarray:
        return self.decoder.decode_batch_bit_packed(bit_packed_detection_event_data)


class SinterUnionFindDecoder(sinter.Decoder):
    """A sinter decoder backed by the C++ Union-Find decoder."""

    def __init__(self, weighted: bool = True, resolution: int = 1024):
        self.weighted = weighted
        self.resolution = resolution

    def compile_decoder_for_dem(self, *, dem: stim.DetectorErrorModel) -> sinter.CompiledDecoder:
        return _CompiledUnionFind(UnionFindDecoder.from_dem(
            dem, weighted=self.weighted, resolution=self.resolution))

    def decode_via_files(self, *, num_shots, num_dets, num_obs, dem_path,
                         dets_b8_in_path, obs_predictions_b8_out_path, tmp_dir):
        # Fallback path for sinter versions that do not compile decoders.
        dem = stim.DetectorErrorModel.from_file(dem_path)
        decoder = UnionFindDecoder.from_dem(dem, weighted=self.weighted,
                                            resolution=self.resolution)
        dets = stim.read_shot_data_file(path=dets_b8_in_path, format="b8",
                                        bit_packed=True, num_detectors=num_dets)
        preds = decoder.decode_batch(np.unpackbits(
            dets, axis=1, count=num_dets, bitorder="little"))
        stim.write_shot_data_file(data=preds, path=obs_predictions_b8_out_path,
                                  format="b8", num_observables=num_obs)


def sinter_decoders() -> dict[str, sinter.Decoder]:
    """Decoders to pass as sinter's custom_decoders."""
    return {
        "backlog-uf": SinterUnionFindDecoder(weighted=True),
        "backlog-uf-unweighted": SinterUnionFindDecoder(weighted=False),
    }
