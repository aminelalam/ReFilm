from __future__ import annotations

from typing import Any


DEFAULT_PROFILE = "quality"
COLOR_ENHANCEMENT_FILTER = "eq=contrast=1.02:saturation=1.05:gamma=1.01"
DEFAULT_COLOR_MODE = "none"
DEFAULT_COLOR_STYLE = "historical_natural"

COLOR_MODES: dict[str, dict[str, str]] = {
    "none": {
        "label": "No color",
        "description": "Restores luminance, noise, and sharpness without changing the chromatic intent.",
    },
    "enhance": {
        "label": "Classic enhance",
        "description": "Conservative contrast, gamma, and saturation adjustment for already colored material.",
    },
    "ai_natural": {
        "label": "Natural AI color",
        "description": "Photorealistic neural colorization with a natural historical style.",
    },
}

PROFILES: dict[str, dict[str, Any]] = {
    "fast": {
        "label": "Fast",
        "description": "Low-cost classic preview with a single encode pass.",
        "crf": 23,
        "preset": "veryfast",
        "denoise": "hqdn3d=1.5:1.25:2:1.5",
        "deband": None,
        "sharpen": "unsharp=5:5:0.3:5:5:0",
        "tone": None,
        "scale_flags": "lanczos",
    },
    "quality": {
        "label": "Quality",
        "description": "Balanced render with cleanup, reduced banding, and controlled detail.",
        "crf": 17,
        "preset": "medium",
        "denoise": "hqdn3d=2.2:1.8:3.2:2.4",
        "deband": "deband=range=14:blur=1:coupling=1",
        "sharpen": "unsharp=5:5:0.34:5:5:0",
        "tone": "eq=contrast=1.025:gamma=1.01",
        "scale_flags": "spline",
    },
    "premium": {
        "label": "Premium",
        "description": "Maximum local quality: slower, with stronger denoise and finer compression.",
        "crf": 16,
        "preset": "slow",
        "denoise": "hqdn3d=2.8:2.2:4.0:3.0",
        "deband": "deband=range=18:blur=1:coupling=1",
        "sharpen": "unsharp=5:5:0.28:5:5:0",
        "tone": "eq=contrast=1.02:gamma=1.01",
        "scale_flags": "spline",
    },
    "ai_realesrgan": {
        "label": "AI Real-ESRGAN",
        "description": "Vulkan neural super-resolution. Much slower; use it for selected clips.",
        "model": "realesrgan-x4plus",
        "scale": 2,
    },
}


def normalize_profile(value: str | None) -> str:
    profile = (value or DEFAULT_PROFILE).strip().lower()
    if profile not in PROFILES:
        allowed = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown restoration profile: {profile}. Allowed: {allowed}")
    return profile


def normalize_color_mode(value: str | None, *, legacy_colorize: bool = False) -> str:
    if value is None:
        return "ai_natural" if legacy_colorize else DEFAULT_COLOR_MODE
    color_mode = value.strip().lower()
    if color_mode in {"true", "yes", "on", "1"}:
        return "ai_natural"
    if color_mode in {"false", "no", "off", "0"}:
        return "none"
    if color_mode not in COLOR_MODES:
        allowed = ", ".join(sorted(COLOR_MODES))
        raise ValueError(f"Unknown color mode: {color_mode}. Allowed: {allowed}")
    return color_mode


def public_color_modes() -> list[dict[str, str]]:
    return [
        {
            "id": color_mode,
            "label": settings["label"],
            "description": settings["description"],
        }
        for color_mode, settings in COLOR_MODES.items()
    ]


def public_profiles() -> list[dict[str, str]]:
    return [
        {
            "id": profile_id,
            "label": str(settings["label"]),
            "description": str(settings["description"]),
        }
        for profile_id, settings in PROFILES.items()
    ]
