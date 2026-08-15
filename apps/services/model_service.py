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

from apps.config import MODEL_PATH, LABELS_PATH, CLASS_MAPPING_PATH

logger = logging.getLogger("waste_classifier.model_service")


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
        """
        Loads the Keras model and label mappings into memory ONCE during server startup.
        """
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
                # Load the TFLite interpreter model
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

                # Load labels or class mapping
                self._labels = self._load_class_labels()
                self._is_loaded = True

                logger.info(
                    f"Model loaded successfully! Classes ({len(self._labels)}): {self._labels}"
                )
            except Exception as e:
                logger.critical(f"Failed to load TFLite model: {str(e)}", exc_info=True)
                raise RuntimeError(f"Could not load waste classification model: {str(e)}")

    def _load_class_labels(self) -> List[str]:
        """
        Loads class names from class_mapping.json or labels.txt.
        """
        # Try loading class_mapping.json first if available
        if os.path.exists(CLASS_MAPPING_PATH):
            try:
                with open(CLASS_MAPPING_PATH, "r", encoding="utf-8") as f:
                    mapping = json.load(f)
                    # Convert dict {"0": "Biodegradable", ...} to ordered list by index
                    sorted_pairs = sorted(mapping.items(), key=lambda item: int(item[0]))
                    return [pair[1] for pair in sorted_pairs]
            except Exception as e:
                logger.warning(f"Failed reading class_mapping.json ({e}), falling back to labels.txt")

        # Fallback to labels.txt
        if os.path.exists(LABELS_PATH):
            with open(LABELS_PATH, "r", encoding="utf-8") as f:
                labels = [line.strip() for line in f.readlines() if line.strip()]
                if labels:
                    return labels

        # Default fallback list if no external label files exist
        return ["Biodegradable", "Electronic", "Hazardous", "Recyclable", "Residual"]

    def _predict_sync(self, tensor_batch: np.ndarray) -> Dict[str, Any]:
        """
        Synchronous thread-safe model inference operation.
        """
        if not self._is_loaded or self._model is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Model is not loaded."
            )

        start_time = time.perf_counter()

        with self._inference_lock:
            # Run inference on the TFLite interpreter
            self._model.set_tensor(self._input_details[0]["index"], tensor_batch)
            self._model.invoke()
            raw_predictions = self._model.get_tensor(self._output_details[0]["index"])[0]

        # Apply Softmax if probabilities don't already sum to 1.0 (logits safeguard)
        if not np.isclose(np.sum(raw_predictions), 1.0, atol=1e-2):
            exp_preds = np.exp(raw_predictions - np.max(raw_predictions))
            probabilities = exp_preds / np.sum(exp_preds)
        else:
            probabilities = raw_predictions

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        # Get highest probability index & class name
        top_idx = int(np.argmax(probabilities))
        
        if top_idx < len(self._labels):
            predicted_class = self._labels[top_idx]
        else:
            predicted_class = f"Class_{top_idx}"

        top_confidence_pct = round(float(probabilities[top_idx]) * 100, 2)

        # Build dictionary of all class probabilities
        prob_dict: Dict[str, float] = {}
        for idx, prob in enumerate(probabilities):
            class_name = self._labels[idx] if idx < len(self._labels) else f"Class_{idx}"
            prob_dict[class_name] = round(float(prob), 4)

        return {
            "success": True,
            "prediction": predicted_class,
            "confidence": top_confidence_pct,
            "probabilities": prob_dict,
            "processing_time_ms": elapsed_ms
        }

    async def predict_async(self, tensor_batch: np.ndarray) -> Dict[str, Any]:
        """
        Asynchronous wrapper around thread-safe model inference using asyncio.to_thread
        to prevent blocking the main asyncio event loop.
        """
        try:
            return await asyncio.to_thread(self._predict_sync, tensor_batch)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"TensorFlow inference error: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Inference execution failed: {str(e)}"
            )

    def get_info(self) -> Dict[str, Any]:
        """
        Returns model metadata, status, backbone architecture, and label categories.
        """
        return {
            "status": "loaded" if self._is_loaded else "unloaded",
            "model_file": os.path.basename(MODEL_PATH),
            "backbone": "MobileNetV2",
            "input_shape": [224, 224, 3],
            "num_classes": len(self._labels),
            "classes": self._labels,
            "framework": "TensorFlow Lite (TFLite)"
        }


# Global singleton service accessor
model_service = WasteClassifierService()
