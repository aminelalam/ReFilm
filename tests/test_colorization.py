import numpy as np

from app.colorization import _limit_chroma, _lock_chroma_by_regions, _style_settings


def test_historical_color_style_limits_excessive_chroma() -> None:
    settings = _style_settings("historical_natural")
    predicted_ab = np.full((8, 8, 2), 80.0, dtype=np.float32)

    limited = _limit_chroma(predicted_ab, float(settings["max_chroma"]))
    magnitude = np.linalg.norm(limited, axis=2)

    assert float(magnitude.max()) <= float(settings["max_chroma"]) + 0.001


def test_region_lock_reduces_chroma_noise_inside_uniform_zones() -> None:
    source = np.full((48, 64, 3), 120, dtype=np.uint8)
    source[:, 32:] = 180
    rng = np.random.default_rng(123)
    noisy_ab = rng.normal(0, 10, (48, 64, 2)).astype(np.float32)

    locked = _lock_chroma_by_regions(source, noisy_ab)

    assert float(locked[:, :30, :].std()) < float(noisy_ab[:, :30, :].std())
    assert float(locked[:, 34:, :].std()) < float(noisy_ab[:, 34:, :].std())
