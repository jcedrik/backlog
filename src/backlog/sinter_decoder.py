"""sinter integration.

Use in Python:

    sinter.collect(..., decoders=["backlog-uf"],
                   custom_decoders=backlog.sinter_decoders())

or from the sinter command line:

    sinter collect ... --decoders backlog-uf \\
        --custom_decoders_module_function "backlog:sinter_decoders"

Correlated decoding is "backlog-uf-corr" (standard, alpha = 1). The
regularized rule takes the reweighting strength explicitly:

    custom_decoders={"uf-rcorr": backlog.SinterUnionFindDecoder(correlated=True, alpha=0.5)}

The sliding-window decoder needs window sizes, so it is built explicitly:

    custom_decoders={"uf-window": backlog.SinterSlidingWindowDecoder(commit=5, buffer=5)}
"""
from __future__ import annotations

import numpy as np
import sinter
import stim

from .decoder import ParallelWindowDecoder, SlidingWindowDecoder, UnionFindDecoder


class _Compiled(sinter.CompiledDecoder):
    def __init__(self, decoder):
        self.decoder = decoder

    def decode_shots_bit_packed(self, *, bit_packed_detection_event_data: np.ndarray) -> np.ndarray:
        return self.decoder.decode_batch_bit_packed(bit_packed_detection_event_data)


class SinterUnionFindDecoder(sinter.Decoder):
    """A sinter decoder backed by the C++ Union-Find decoder."""

    def __init__(self, weighted: bool = True, resolution: int = 1024,
                 correlated: bool = False, alpha: float = 1.0):
        self.weighted = weighted
        self.resolution = resolution
        self.correlated = correlated
        self.alpha = alpha

    def _options(self) -> dict:
        return dict(weighted=self.weighted, resolution=self.resolution,
                    correlated=self.correlated, alpha=self.alpha)

    def _make(self, dem: stim.DetectorErrorModel):
        return UnionFindDecoder.from_dem(dem, **self._options())

    def compile_decoder_for_dem(self, *, dem: stim.DetectorErrorModel) -> sinter.CompiledDecoder:
        return _Compiled(self._make(dem))

    def decode_via_files(self, *, num_shots, num_dets, num_obs, dem_path,
                         dets_b8_in_path, obs_predictions_b8_out_path, tmp_dir):
        # Fallback path for sinter versions that do not compile decoders.
        dem = stim.DetectorErrorModel.from_file(dem_path)
        decoder = self._make(dem)
        dets = stim.read_shot_data_file(path=dets_b8_in_path, format="b8",
                                        bit_packed=True, num_detectors=num_dets)
        preds = decoder.decode_batch(np.unpackbits(
            dets, axis=1, count=num_dets, bitorder="little"))
        stim.write_shot_data_file(data=preds, path=obs_predictions_b8_out_path,
                                  format="b8", num_observables=num_obs)


class SinterSlidingWindowDecoder(SinterUnionFindDecoder):
    """A sinter decoder backed by the C++ sliding-window decoder.

    commit and buffer are in time layers (rounds). Detector layers come from
    the detector coordinates of the circuit.
    """

    def __init__(self, commit: int, buffer: int, weighted: bool = True,
                 resolution: int = 1024, correlated: bool = False, alpha: float = 1.0):
        super().__init__(weighted=weighted, resolution=resolution,
                         correlated=correlated, alpha=alpha)
        self.commit = commit
        self.buffer = buffer

    def _make(self, dem: stim.DetectorErrorModel):
        return SlidingWindowDecoder.from_dem(dem, commit=self.commit, buffer=self.buffer,
                                             **self._options())


class SinterParallelWindowDecoder(SinterUnionFindDecoder):
    """A sinter decoder backed by the C++ parallel window decoder.

    sinter already runs one decoder per worker process, so threads defaults
    to 1 here; the answer does not depend on it.
    """

    def __init__(self, commit: int, buffer: int, seam: int | None = None,
                 threads: int = 1, weighted: bool = True, resolution: int = 1024,
                 correlated: bool = False, alpha: float = 1.0):
        super().__init__(weighted=weighted, resolution=resolution,
                         correlated=correlated, alpha=alpha)
        self.commit = commit
        self.buffer = buffer
        self.seam = seam
        self.threads = threads

    def _make(self, dem: stim.DetectorErrorModel):
        return ParallelWindowDecoder.from_dem(dem, commit=self.commit, buffer=self.buffer,
                                              seam=self.seam, threads=self.threads,
                                              **self._options())


def sinter_decoders() -> dict[str, sinter.Decoder]:
    """Decoders to pass as sinter's custom_decoders."""
    return {
        "backlog-uf": SinterUnionFindDecoder(weighted=True),
        "backlog-uf-unweighted": SinterUnionFindDecoder(weighted=False),
        "backlog-uf-corr": SinterUnionFindDecoder(correlated=True),
    }
