# WISE Waste Classifier FastAPI Backend

Production-ready FastAPI backend for waste image classification using a pre-trained **MobileNetV2** TensorFlow / Keras model (`waste_classifier.keras`).

---

## 🌟 Key Features & Performance Optimizations

1. **Single Startup Model Loading**: The Keras model is loaded **ONLY ONCE** during FastAPI server startup using the `lifespan` context manager. It stays in RAM and is never reloaded per request.
2. **Thread-Safe & Non-Blocking Async Inference**: TensorFlow execution is wrapped inside `asyncio.to_thread` with a thread lock to ensure multi-threaded safety without blocking the FastAPI event loop.
3. **Strict Validation & Error Handling**:
   - MIME type checking (`image/jpeg`, `image/png`, `image/webp`).
   - Image size limits (default 10MB).
   - Integrity verification via PIL to reject corrupted/malformed images.
4. **Automatic Cleanup**: Scratch/temporary files uploaded during prediction processing are deleted immediately in a `finally` block.
5. **Exact MobileNetV2 Preprocessing**: Converts inputs to RGB, resizes to `224x224`, normalizes pixel values to `[-1, 1]` via `(x / 127.5) - 1.0`, and expands batch dimension.
6. **Class Mapping**:
   - `0`: Biodegradable
   - `1`: Electronic Waste
   - `2`: Hazardous
   - `3`: Recyclable
   - `4`: Residual (Non-Recyclable)

---

## 📂 Project Architecture

```text
backend/
├── apps/
│   ├── main.py                    # FastAPI application initialization & lifespan context
│   ├── config.py                  # Environment & model configuration settings
│   ├── database.py                # Database connection configuration
│   ├── ai_model/
│   │   ├── waste_classifier.keras # Pre-trained Keras model
│   │   ├── class_mapping.json     # Index-to-class dictionary mapping
│   │   └── labels.txt             # Plain-text class names
│   ├── routers/
│   │   ├── prediction.py          # POST /api/v1/predict endpoint
│   │   └── health_model.py        # GET /health & GET /model/info endpoints
│   ├── services/
│   │   └── model_service.py       # Singleton WasteClassifierService (thread-safe inference)
│   └── utils/
│       └── image_processor.py     # Image validation & MobileNetV2 preprocessing
├── uploads/                       # Temporary scratch directory (auto-cleaned)
├── requirements.txt               # Dependencies
├── Dockerfile                     # Container deployment image definition
├── .env                           # Environment secrets & paths
├── .gitignore
└── README.md
```

---

## 🚀 Local Setup & Execution

### 1. Requirements
- Python 3.10+ (or Python 3.12+)
- Virtual environment

### 2. Installation
```bash
# Create and activate virtual environment
python -m venv venv
# On Windows PowerShell:
.\venv\Scripts\Activate.ps1
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Run Server
```bash
uvicorn apps.main:app --reload --host 0.0.0.0 --port 8000
```
Interactive Swagger API documentation will be available at: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🌐 API Reference

### 1. Waste Prediction Endpoint
`POST /api/v1/predict`
- **Content-Type**: `multipart/form-data`
- **Form Field**: `image` (File)

#### Sample Response (`200 OK`)
```json
{
    "success": true,
    "prediction": "Recyclable",
    "confidence": 98.74,
    "probabilities": {
        "Biodegradable": 0.0100,
        "Electronic Waste": 0.0000,
        "Hazardous": 0.0000,
        "Recyclable": 0.9874,
        "Residual (Non-Recyclable)": 0.0026
    },
    "processing_time_ms": 42.15
}
```

### 2. Health Endpoint
`GET /health`
```json
{
    "status": "healthy",
    "database": "connected",
    "model": "loaded",
    "model_file": "waste_classifier.keras"
}
```

### 3. Model Information Endpoint
`GET /model/info`
```json
{
    "status": "loaded",
    "model_file": "waste_classifier.keras",
    "backbone": "MobileNetV2",
    "input_shape": [224, 224, 3],
    "num_classes": 5,
    "classes": [
        "Biodegradable",
        "Electronic Waste",
        "Hazardous",
        "Recyclable",
        "Residual (Non-Recyclable)"
    ],
    "framework": "TensorFlow 2.15.0 (Keras)"
}
```

---

## 💻 Integration Examples

### 1. `curl` Examples

#### Test Prediction Endpoint
```bash
curl -X POST "http://localhost:8000/api/v1/predict" \
     -H "accept: application/json" \
     -H "Content-Type: multipart/form-data" \
     -F "image=@/path/to/waste_sample.jpg"
```

#### Test Health Endpoint
```bash
curl -X GET "http://localhost:8000/health"
```

#### Test Model Info Endpoint
```bash
curl -X GET "http://localhost:8000/model/info"
```

---

### 2. Postman Configuration

1. Open Postman and create a new request.
2. Set Method to **`POST`**.
3. URL: `http://localhost:8000/api/v1/predict`
4. Navigate to **Body** -> Select **form-data**.
5. Key: `image` -> Select type as **File** (instead of Text).
6. Value: Click **Select Files** and choose a `.jpg` or `.png` image.
7. Click **Send**.

---

### 3. Android Kotlin Integration Example (Retrofit + OkHttp)

#### Interface (`WasteApiService.kt`)
```kotlin
import okhttp3.MultipartBody
import retrofit2.Response
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part

data class PredictionResponse(
    val success: Boolean,
    val prediction: String,
    val confidence: Double,
    val probabilities: Map<String, Double>,
    val processing_time_ms: Double
)

interface WasteApiService {
    @Multipart
    @POST("api/v1/predict")
    suspend fun predictWasteCategory(
        @Part image: MultipartBody.Part
    ): Response<PredictionResponse>
}
```

#### Repository / Usage (`WasteRepository.kt`)
```kotlin
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File

class WasteRepository(private val apiService: WasteApiService) {

    suspend fun classifyWasteImage(imageFile: File): Result<PredictionResponse> {
        return try {
            val requestFile = imageFile.asRequestBody("image/jpeg".toMediaTypeOrNull())
            val body = MultipartBody.Part.createFormData("image", imageFile.name, requestFile)

            val response = apiService.predictWasteCategory(body)
            if (response.isSuccessful && response.body() != null) {
                Result.success(response.body()!!)
            } else {
                Result.failure(Exception("Prediction failed with code: ${response.code()}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}
```

---

## 🚢 Production Deployment

### Deployment on Docker
```bash
# Build docker image
docker build -t wise-waste-backend .

# Run docker container
docker run -d -p 8000:8000 --name wise-backend wise-waste-backend
```

### Deployment on Render / Railway / VPS
1. Set start command:
   ```bash
   uvicorn apps.main:app --host 0.0.0.0 --port $PORT
   ```
2. Set Environment variables in host panel:
   - `MODEL_PATH`: `apps/ai_model/waste_classifier.keras`
   - `MAX_UPLOAD_SIZE_MB`: `10`
