from fastapi.testclient import TestClient
import cv2
import numpy as np

from app.main import app


client = TestClient(app)


def test_home_loads() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "ReFilm" in response.text


def test_rejects_invalid_video_extension() -> None:
    response = client.post(
        "/api/videos",
        files={"video": ("notes.txt", b"not a video", "text/plain")},
        data={"colorize": "false"},
    )
    assert response.status_code == 400
    assert "Invalid video extension" in response.json()["detail"]


def test_lists_restoration_profiles() -> None:
    response = client.get("/api/profiles")
    assert response.status_code == 200
    assert {profile["id"] for profile in response.json()} == {"fast", "quality", "premium", "ai_realesrgan"}


def test_lists_color_modes() -> None:
    response = client.get("/api/color-modes")
    assert response.status_code == 200
    assert {mode["id"] for mode in response.json()} == {"none", "enhance", "ai_natural"}


def test_rejects_invalid_restoration_profile() -> None:
    response = client.post(
        "/api/videos",
        files={"video": ("clip.mp4", b"not used", "video/mp4")},
        data={"profile": "unknown"},
    )
    assert response.status_code == 400
    assert "Unknown restoration profile" in response.json()["detail"]


def test_rejects_unreadable_video_content() -> None:
    response = client.post(
        "/api/videos",
        files={"video": ("clip.mp4", b"not a real video", "video/mp4")},
        data={"profile": "quality"},
    )
    assert response.status_code == 400
    assert "not a readable video" in response.json()["detail"]


def test_rejects_invalid_image_extension() -> None:
    response = client.post(
        "/api/images",
        files={"image": ("notes.txt", b"not an image", "text/plain")},
        data={"color_mode": "none"},
    )
    assert response.status_code == 400
    assert "Invalid image extension" in response.json()["detail"]


def test_evaluates_restored_image_against_reference() -> None:
    image = np.full((32, 48, 3), 127, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    image_bytes = encoded.tobytes()

    response = client.post(
        "/api/dataset/images/evaluate",
        files={
            "clean_image": ("clean.png", image_bytes, "image/png"),
            "restored_image": ("restored.png", image_bytes, "image/png"),
        },
    )

    assert response.status_code == 200
    details = response.json()["details"]
    assert details["ssim"] == 1.0
    assert details["candidate_resized_for_metrics"] is False
