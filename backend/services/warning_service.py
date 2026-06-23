import os
import logging
import httpx

logger = logging.getLogger(__name__)

# Configurable Sarvam API details
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
SARVAM_BASE_URL = "https://api.sarvam.ai"

# Recommended action translations for templates
ACTION_TRANSLATIONS = {
    "Reduce speed": {
        "en": "Reduce speed",
        "hi": "गति कम करें",
        "hinglish": "Speed kam karein"
    },
    "Move left": {
        "en": "Move left",
        "hi": "बाईं ओर चलें",
        "hinglish": "Left move karein"
    },
    "Move right": {
        "en": "Move right",
        "hi": "दाईं ओर चलें",
        "hinglish": "Right move karein"
    },
    "Exercise caution": {
        "en": "Exercise caution",
        "hi": "सावधानी बरतें",
        "hinglish": "Caution rakhein"
    }
}

HAZARD_LABEL_TRANSLATIONS = {
    "stationary_vehicle": {
        "en": "Stationary vehicle",
        "hi": "रुका हुआ वाहन",
        "hinglish": "stationary vehicle"
    },
    "pothole": {
        "en": "Pothole",
        "hi": "गड्ढा",
        "hinglish": "pothole"
    },
    "road_obstruction": {
        "en": "Road obstruction",
        "hi": "सड़क बाधा",
        "hinglish": "road obstruction"
    },
    "unknown": {
        "en": "Hazard",
        "hi": "खतरा",
        "hinglish": "hazard"
    }
}

class WarningService:
    @staticmethod
    def get_action_translation(action: str, lang: str) -> str:
        if action in ACTION_TRANSLATIONS:
            return ACTION_TRANSLATIONS[action].get(lang, action)
        return action

    @staticmethod
    def get_label_translation(hazard_type: str, lang: str) -> str:
        if hazard_type in HAZARD_LABEL_TRANSLATIONS:
            return HAZARD_LABEL_TRANSLATIONS[hazard_type].get(lang, hazard_type)
        return HAZARD_LABEL_TRANSLATIONS.get("unknown", {}).get(lang, "hazard")

    @classmethod
    def generate_warning_texts(cls, hazard_type: str, distance_meters: int, recommended_action: str) -> dict:
        """Generates warning strings for a hazard in en, hi, and hinglish."""
        # Clean hazard_type
        h_type = hazard_type.lower()
        if h_type not in HAZARD_LABEL_TRANSLATIONS:
            h_type = "unknown"

        # Translate action and label
        action_en = recommended_action
        action_hi = cls.get_action_translation(recommended_action, "hi")
        action_hinglish = cls.get_action_translation(recommended_action, "hinglish")

        label_en = cls.get_label_translation(h_type, "en")
        label_hi = cls.get_label_translation(h_type, "hi")
        label_hinglish = cls.get_label_translation(h_type, "hinglish")

        # Compile templates
        if h_type == "stationary_vehicle":
            en_text = f"Stationary vehicle approximately {distance_meters} metres ahead. {action_en}."
            hi_text = f"लगभग {distance_meters} मीटर आगे एक रुका हुआ वाहन है। {action_hi}।"
            hinglish_text = f"{distance_meters} metre aage stationary vehicle hai. {action_hinglish}."
        elif h_type == "pothole":
            en_text = f"Pothole approximately {distance_meters} metres ahead. {action_en}."
            hi_text = f"लगभग {distance_meters} मीटर आगे एक गड्ढा है। {action_hi}।"
            hinglish_text = f"{distance_meters} metre aage pothole hai. {action_hinglish}."
        else:
            en_text = f"Hazard approximately {distance_meters} metres ahead. {action_en}."
            hi_text = f"लगभग {distance_meters} मीटर आगे खतरा है। {action_hi}।"
            hinglish_text = f"{distance_meters} metre aage hazard hai. {action_hinglish}."

        return {
            "en": en_text,
            "hi": hi_text,
            "hinglish": hinglish_text
        }

    @classmethod
    async def synthesize_speech_sarvam(cls, text: str, language_code: str = "en-IN") -> bytes:
        """Calls the Sarvam AI Text-to-Speech API if credentials are provided.
        
        Otherwise, raises ValueError or returns mock audio data for testing.
        """
        if not SARVAM_API_KEY:
            raise ValueError("Sarvam API key not configured")

        headers = {
            "api-subscription-key": SARVAM_API_KEY,
            "Content-Type": "application/json"
        }
        
        payload = {
            "text": text,
            "target_language_code": language_code,
            "speaker": "shubh",
            "pitch": 1.0,
            "pace": 1.0,
            "loudness": 1.0,
            "sample_rate": 24000,
            "output_audio_codec": "mp3"
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{SARVAM_BASE_URL}/text-to-speech",
                    json=payload,
                    headers=headers,
                    timeout=10.0
                )
                if response.status_code == 200:
                    return response.content
                else:
                    logger.error(f"Sarvam API error: {response.status_code} - {response.text}")
                    raise ValueError(f"Sarvam API error: {response.text}")
            except Exception as e:
                logger.error(f"Failed to connect to Sarvam: {e}")
                raise
