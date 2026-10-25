import os
import logging
from functools import wraps
from flask import Flask, render_template, request, jsonify
import numpy as np
import cv2
import base64
import requests
from tensorflow.keras.layers import DepthwiseConv2D
from tensorflow.keras.models import load_model
from services.gemini_service import get_classification_guidance, get_home_insights
from services.recycler_service import get_nearby_recyclers
from services.sortbot_service import sortbot_service

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# Reject oversized payloads early (default 8 MB, configurable via env).
MAX_CONTENT_LENGTH_MB = max(1, int(os.environ.get("MAX_CONTENT_LENGTH_MB", "8")))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH_MB * 1024 * 1024

# Load trained model path from environment, fallback to project default
MODEL_PATH = os.environ.get("MODEL_PATH", "plastic-ai-project/model/plastic_model.h5")
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()
SUPABASE_USERINFO_URL = (
    f"{SUPABASE_URL.rstrip('/')}/auth/v1/user" if SUPABASE_URL else ""
)

class _LegacyDepthwiseConv2D(DepthwiseConv2D):
    def __init__(self, *args, groups=1, **kwargs):
        if groups != 1:
            raise ValueError("Only groups=1 is supported for the bundled model.")
        super().__init__(*args, **kwargs)


model = load_model(
    MODEL_PATH,
    custom_objects={"DepthwiseConv2D": _LegacyDepthwiseConv2D},
)
# ⚠️ MUST MATCH training order exactly
CLASS_NAMES = ['HDPE', 'LDPE', 'OTHER', 'PET', 'PP', 'PS', 'PVC']


def _extract_bearer_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


def _get_authenticated_user(token: str):
    if not SUPABASE_USERINFO_URL or not SUPABASE_ANON_KEY:
        app.logger.error("Supabase auth is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY.")
        return None

    try:
        response = requests.get(
            SUPABASE_USERINFO_URL,
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {token}",
            },
            timeout=8,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        if isinstance(payload, dict) and payload.get("id"):
            return payload
    except Exception:
        app.logger.exception("Supabase token verification failed")
    return None


def require_auth(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        token = _extract_bearer_token()
        if not token:
            return jsonify({"error": "Authentication required."}), 401

        user = _get_authenticated_user(token)
        if not user:
            return jsonify({"error": "Invalid or expired authentication token."}), 401

        return view_func(*args, **kwargs)

    return wrapper


@app.route("/")
def index():
    insights = get_home_insights()
    return render_template(
        "index.html",
        home_tip=insights.get("tip", ""),
        home_fact=insights.get("fact", ""),
        supabase_url=SUPABASE_URL,
        supabase_anon_key=SUPABASE_ANON_KEY,
    )


@app.route("/predict", methods=["POST"])
@require_auth
def predict():
    try:
        body = request.get_json(silent=True) or {}
        data = body.get("image")
        if not isinstance(data, str) or "," not in data:
            return jsonify({"error": "Invalid image payload."}), 400

        # Decode base64 image
        img_data = base64.b64decode(data.split(",")[1])
        np_img = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(np_img, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({"error": "Unable to decode image."}), 400

        # Preprocess
        img = cv2.resize(img, (224, 224))
        img = img / 255.0
        img = np.expand_dims(img, axis=0)

        # Predict
        preds = model.predict(img)
        class_index = int(np.argmax(preds))
        label = CLASS_NAMES[class_index]
        confidence = float(np.max(preds)) * 100

        guidance = get_classification_guidance(label, confidence)

        return jsonify({
            "type": label,
            "confidence": round(confidence, 2),
            "sortbot": sortbot_service.prediction_target(label, confidence),
            "reuse": guidance.get("reuse_ideas", []),
            "recycle_instructions": guidance.get("recycling_instructions", []),
            "ai_advice": guidance.get("ai_advice", ""),
        })

    except Exception:
        app.logger.exception("Prediction failed")
        return jsonify({"error": "Prediction failed. Please try again."}), 500


@app.route("/recyclers", methods=["POST"])
@require_auth
def recyclers():
    try:
        body = request.get_json(silent=True) or {}
        lat = float(body["latitude"])
        lon = float(body["longitude"])

        radius = body.get("radius")
        if radius is not None:
            radius = max(500, int(radius))
            results = get_nearby_recyclers(lat, lon, radius=radius)
        else:
            results = get_nearby_recyclers(lat, lon)

        if isinstance(results, dict) and "error" in results:
            return jsonify(results), 502

        return jsonify(results)
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({"error": f"Invalid request: {e}"}), 400
    except Exception:
        app.logger.exception("Recycler lookup failed")
        return jsonify({"error": "Unable to fetch recycling centres right now."}), 500


@app.route("/sortbot/status", methods=["GET"])
@require_auth
def sortbot_status():
    return jsonify(sortbot_service.status())


@app.route("/sort", methods=["POST"])
@require_auth
def sort():
    try:
        body = request.get_json(silent=True) or {}
        plastic_type = body.get("type")
        confidence = float(body["confidence"])
        if not isinstance(plastic_type, str) or not plastic_type.strip():
            return jsonify({"error": "A predicted plastic type is required."}), 400
        operation = sortbot_service.send_sort(plastic_type, confidence)
        return jsonify(operation), 202
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception:
        app.logger.exception("SortBot command failed")
        return jsonify({"error": "Unable to send the SortBot command."}), 500


@app.route("/sort/status/<operation_id>", methods=["GET"])
@require_auth
def sort_status(operation_id):
    operation = sortbot_service.get_operation(operation_id)
    if not operation:
        return jsonify({"error": "Sorting operation not found."}), 404
    return jsonify(operation)


@app.route("/home-insights", methods=["GET"])
def home_insights():
    try:
        insights = get_home_insights()
        response = jsonify({
            "tip": insights.get("tip", ""),
            "fact": insights.get("fact", ""),
        })
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception:
        app.logger.exception("Home insights failed")
        return jsonify({"error": "Unable to load insights right now."}), 500


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(self), camera=(self)",
    )
    return response


if __name__ == "__main__":
    app.run(debug=FLASK_DEBUG)