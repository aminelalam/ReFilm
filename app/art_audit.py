from __future__ import annotations

import uuid
from pathlib import Path

import cv2
import numpy as np
from skimage.metrics import structural_similarity

from app.audit import AuditStore
from app.storage import LocalBucket


class ArtworkAuditor:
    def __init__(self, audit: AuditStore | None = None, bucket: LocalBucket | None = None) -> None:
        self.audit = audit or AuditStore()
        self.bucket = bucket or LocalBucket()

    def run(self, reference_path: Path, current_path: Path) -> dict:
        audit_id = uuid.uuid4().hex
        reference = cv2.imread(str(reference_path))
        current = cv2.imread(str(current_path))
        if reference is None or current is None:
            raise ValueError("Could not read one of the uploaded images.")

        aligned = self._align_to_reference(reference, current)
        gray_ref = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
        gray_cur = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
        score, diff = structural_similarity(gray_ref, gray_cur, full=True)
        diff_map = ((1 - diff) * 255).astype("uint8")
        heatmap = cv2.applyColorMap(diff_map, cv2.COLORMAP_JET)
        overlay = cv2.addWeighted(aligned, 0.65, heatmap, 0.35, 0)

        heatmap_path = self.bucket.art_path(audit_id, "difference_heatmap.jpg")
        cv2.imwrite(str(heatmap_path), overlay)

        changed_pixels = int(np.count_nonzero(diff_map > 60))
        total_pixels = int(diff_map.size)
        change_ratio = changed_pixels / total_pixels if total_pixels else 0.0
        risk = "low"
        if score < 0.82 or change_ratio > 0.08:
            risk = "high"
        elif score < 0.92 or change_ratio > 0.03:
            risk = "medium"

        report = {
            "id": audit_id,
            "ssim": round(float(score), 4),
            "changed_pixel_ratio": round(float(change_ratio), 4),
            "risk": risk,
            "interpretation": self._interpret(risk),
            "heatmap_path": str(heatmap_path),
        }
        self.audit.save_art_audit(audit_id, reference_path, current_path, heatmap_path, report, "completed")
        return report

    def _align_to_reference(self, reference: np.ndarray, current: np.ndarray) -> np.ndarray:
        current = cv2.resize(current, (reference.shape[1], reference.shape[0]))
        ref_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
        cur_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)

        orb = cv2.ORB_create(1000)
        keypoints_ref, descriptors_ref = orb.detectAndCompute(ref_gray, None)
        keypoints_cur, descriptors_cur = orb.detectAndCompute(cur_gray, None)
        if descriptors_ref is None or descriptors_cur is None:
            return current

        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = sorted(matcher.match(descriptors_ref, descriptors_cur), key=lambda m: m.distance)
        if len(matches) < 12:
            return current

        src = np.float32([keypoints_cur[m.trainIdx].pt for m in matches[:80]]).reshape(-1, 1, 2)
        dst = np.float32([keypoints_ref[m.queryIdx].pt for m in matches[:80]]).reshape(-1, 1, 2)
        matrix, _ = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if matrix is None:
            return current
        return cv2.warpPerspective(current, matrix, (reference.shape[1], reference.shape[0]))

    def _interpret(self, risk: str) -> str:
        if risk == "high":
            return "Possible significant visual change; review manually before conservation decisions."
        if risk == "medium":
            return "Moderate visual change; inspect highlighted areas."
        return "No relevant visual deterioration detected by the MVP comparator."
