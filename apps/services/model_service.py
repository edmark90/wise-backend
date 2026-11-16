import os
import time
import json
import logging
import threading
import asyncio
from typing import Dict, Any, List, Optional
import numpy as np
from fastapi import HTTPException, status

try:
    import tflite_runtime.interpreter as tflite
    InterpreterCls = tflite.Interpreter
except ImportError:
    # Fallback for local dev where full TensorFlow is installed
    from tensorflow.lite.python.interpreter import Interpreter as InterpreterCls

from apps.config import (
    AI_LOW_CONFIDENCE_THRESHOLD,
    AI_TTA_ENABLED,
    CLASS_MAPPING_PATH,
    LABELS_PATH,
    MODEL_PATH,
)

logger = logging.getLogger("waste_classifier.model_service")

# Threshold below which Stage-1 upright views trigger 90/270-degree fallback checks
_ORIENTATION_RETRY_THRESHOLD = 0.72

# Map the display labels (class_mapping.json) to short DB keys (labels.txt).
# Storing the short form keeps waste_records values colon-len bounded and
# aligned with the admin "waste type" filter options.
CLASS_KEY_MAP = {
    "Biodegradable Waste": "Biodegradable",
    "Electronic Waste (E-Waste)": "Electronic",
    "Hazardous Waste": "Hazardous",
    "Recyclable Waste": "Recyclable",
    "Residual (Non-Recyclable) Waste": "Residual",
}


def class_key(label: str) -> str:
    """Short stable key for a model label (falls back to the label unchanged)."""
    return CLASS_KEY_MAP.get(label, label)


class WasteClassifierService:
    _instance: Optional["WasteClassifierService"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(WasteClassifierService, cls).__new__(cls)
                    cls._instance._is_loaded = False
                    cls._instance._model = None
                    cls._instance._input_details = None
                    cls._instance._output_details = None
                    cls._instance._labels: List[str] = []
                    cls._instance._inference_lock = threading.Lock()
        return cls._instance

    def load_model(self) -> None:
        """Load the TFLite interpreter and label mappings into memory ONCE during server startup."""
        if self._is_loaded:
            logger.info("Model is already loaded in memory.")
            return

        with self._lock:
            if self._is_loaded:
                return

            logger.info(f"Loading TFLite model from: {MODEL_PATH}")
            if not os.path.exists(MODEL_PATH):
                raise FileNotFoundError(f"Model file not found at path: {MODEL_PATH}")

            try:
                num_threads = min(4, max(1, os.cpu_count() or 2))
                try:
                    self._model = InterpreterCls(model_path=MODEL_PATH, num_threads=num_threads)
                except TypeError:
                    self._model = InterpreterCls(model_path=MODEL_PATH)

                self._model.allocate_tensors()
                self._input_details = self._model.get_input_details()
                self._output_details = self._model.get_output_details()

                # Warmup inference to initialize the interpreter / XNNPACK delegate
                dummy_input = np.zeros(
                    self._input_details[0]["shape"], dtype=self._input_details[0]["dtype"]
                )
                self._model.set_tensor(self._input_details[0]["index"], dummy_input)
                self._model.invoke()
                _ = self._model.get_tensor(self._output_details[0]["index"])

                self._labels = self._load_class_labels()
                self._is_loaded = True

                logger.info(
                    f"Model loaded successfully! Classes ({len(self._labels)}): {self._labels}"
                )
            except Exception as e:
                logger.critical(f"Failed to load TFLite model: {str(e)}", exc_info=True)
                raise RuntimeError(f"Could not load waste classification model: {str(e)}")

    def _load_class_labels(self) -> List[str]:
        """Load class names from class_mapping.json or labels.txt."""
        if os.path.exists(CLASS_MAPPING_PATH):
            try:
                with open(CLASS_MAPPING_PATH, "r", encoding="utf-8") as f:
                    mapping = json.load(f)
                    sorted_pairs = sorted(mapping.items(), key=lambda item: int(item[0]))
                    return [pair[1] for pair in sorted_pairs]
            except Exception as e:
                logger.warning(f"Failed reading class_mapping.json ({e}), falling back to labels.txt")

        if os.path.exists(LABELS_PATH):
            with open(LABELS_PATH, "r", encoding="utf-8") as f:
                labels = [line.strip() for line in f.readlines() if line.strip()]
                if labels:
                    return labels

        return ["Biodegradable", "Electronic", "Hazardous", "Recyclable", "Residual"]

    def _ensure_loaded(self) -> None:
        if not self._is_loaded or self._model is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Model is not loaded.",
            )

    def _run_single_tensor_locked(self, tensor_batch: np.ndarray) -> np.ndarray:
        """Run single-tensor inference (caller must hold self._inference_lock) and return probability vector."""
        self._model.set_tensor(self._input_details[0]["index"], tensor_batch)
        self._model.invoke()
        raw = self._model.get_tensor(self._output_details[0]["index"])[0]

        if not np.isclose(np.sum(raw), 1.0, atol=1e-2):
            exp_preds = np.exp(raw - np.max(raw))
            probs = exp_preds / np.sum(exp_preds)
        else:
            probs = raw
        return np.asarray(probs, dtype=np.float64)

    def _format_prediction_response(
        self,
        probabilities: np.ndarray,
        elapsed_ms: float,
        quality: Optional[Dict[str, Any]] = None,
        views_evaluated: int = 1,
    ) -> Dict[str, Any]:
        """Build a standardized prediction payload from a normalized probability vector."""
        prob_sum = float(np.sum(probabilities))
        probs = probabilities / prob_sum if prob_sum > 0 else probabilities

        top_idx = int(np.argmax(probs))
        predicted_class = (
            self._labels[top_idx] if top_idx < len(self._labels) else f"Class_{top_idx}"
        )
        top_confidence_pct = round(float(probs[top_idx]) * 100, 2)

        sorted_probs = np.sort(probs)[::-1]
        second_pct = round(float(sorted_probs[1]) * 100, 2) if len(sorted_probs) > 1 else 0.0
        margin_pct = round(max(0.0, top_confidence_pct - second_pct), 2)

        if top_confidence_pct >= 85.0:
            confidence_level = "high"
        elif top_confidence_pct >= AI_LOW_CONFIDENCE_THRESHOLD:
            confidence_level = "medium"
        else:
            confidence_level = "low"

        is_low_confidence = top_confidence_pct < AI_LOW_CONFIDENCE_THRESHOLD

        prob_dict: Dict[str, float] = {}
        for idx, prob in enumerate(probs):
            class_name = self._labels[idx] if idx < len(self._labels) else f"Class_{idx}"
            prob_dict[class_name] = round(float(prob), 4)

        warning = None
        q_warnings = (quality or {}).get("warnings") or []
        if "low_detail" in q_warnings:
            warning = "Image has very low detail or blur; try taking a clearer close-up photo."
        elif "underexposed" in q_warnings and is_low_confidence:
            warning = "Photo is too dark and prediction is uncertain; try better lighting."
        elif is_low_confidence:
            warning = "Low confidence prediction; ensure the single waste item is centered and clearly visible."

        result: Dict[str, Any] = {
            "success": True,
            "prediction": predicted_class,
            "confidence": top_confidence_pct,
            "confidence_level": confidence_level,
            "is_low_confidence": is_low_confidence,
            "confidence_margin": margin_pct,
            "probabilities": prob_dict,
            "processing_time_ms": round(elapsed_ms, 2),
            "views_evaluated": views_evaluated,
        }
        if warning:
            result["warning"] = warning
        if quality is not None:
            result["image_quality"] = quality
        return result

    def _predict_sync(self, tensor_batch: np.ndarray) -> Dict[str, Any]:
        """Synchronous thread-safe single-tensor inference."""
        self._ensure_loaded()
        start_time = time.perf_counter()
        with self._inference_lock:
            probabilities = self._run_single_tensor_locked(tensor_batch)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        return self._format_prediction_response(probabilities, elapsed_ms)

    def predict_pipeline_sync(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """Multi-view ROI/aspect-aware inference with adaptive orientation fallback on uncertainty."""
        self._ensure_loaded()
        views = package.get("views") or []
        if not views:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No preprocessed image views available for inference.",
            )

        if not AI_TTA_ENABLED:
            views = views[:1]

        start_time = time.perf_counter()
        view_probs: List[np.ndarray] = []
        view_weights: List[float] = []

        with self._inference_lock:
            for _name, tensor, weight in views:
                view_probs.append(self._run_single_tensor_locked(tensor))
                view_weights.append(float(weight))

            w_arr = np.array(view_weights, dtype=np.float64)
            w_arr /= w_arr.sum()
            base_probs = (np.array(view_probs) * w_arr[:, None]).sum(axis=0)

            fallback_rot_views = package.get("fallback_rot_views") or []
            if AI_TTA_ENABLED and fallback_rot_views and float(np.max(base_probs)) < _ORIENTATION_RETRY_THRESHOLD:
                for _name, rot_tensor in fallback_rot_views:
                    p_rot = self._run_single_tensor_locked(rot_tensor)
                    view_probs.append(p_rot)
                    view_weights.append(0.22 * float(np.max(p_rot)))

        w_final = np.array(view_weights, dtype=np.float64)
        w_final /= w_final.sum()
        final_probs = (np.array(view_probs) * w_final[:, None]).sum(axis=0)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        return self._format_prediction_response(
            final_probs,
            elapsed_ms,
            quality=package.get("quality"),
            views_evaluated=len(view_probs),
        )

    async def predict_async(self, tensor_batch: np.ndarray) -> Dict[str, Any]:
        """Asynchronous wrapper around single-tensor inference."""
        try:
            return await asyncio.to_thread(self._predict_sync, tensor_batch)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"TensorFlow inference error: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Inference execution failed: {str(e)}",
            )

    async def predict_pipeline_async(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """Asynchronous wrapper around multi-view pipeline inference."""
        try:
            return await asyncio.to_thread(self.predict_pipeline_sync, package)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"TensorFlow pipeline inference error: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Inference execution failed: {str(e)}",
            )

    def get_info(self) -> Dict[str, Any]:
        """Return model metadata, status, backbone architecture, and label categories."""
        input_shape = [224, 224, 3]
        if self._input_details:
            raw_shape = list(int(d) for d in self._input_details[0]["shape"])
            input_shape = raw_shape[1:] if len(raw_shape) == 4 else raw_shape

        return {
            "status": "loaded" if self._is_loaded else "unloaded",
            "model_file": os.path.basename(MODEL_PATH),
            "backbone": "MobileNetV2",
            "input_shape": input_shape,
            "num_classes": len(self._labels),
            "classes": self._labels,
            "framework": "TensorFlow Lite (TFLite)",
            "pipeline": "EXIF+sRGB+MultiView-ROI-TTA" if AI_TTA_ENABLED else "SingleView",
        }


# Global singleton service accessor
model_service = WasteClassifierService()
