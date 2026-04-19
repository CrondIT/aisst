from global_state import (
    MAX_API_TOKEN,
    MAX_BASE_URL,
    GIGACHAT_API_KEY,
    WEBHOOK_URL,
    WEBHOOK_SECRET,
    GIGACHAT_SCOPE,
    ADMIN_API_TOKEN,
    GEMINI_API_KEY,
    MODELS,
)
from gigachat import GigaChat

with GigaChat(
        credentials=GIGACHAT_API_KEY,
        scope=GIGACHAT_SCOPE,
        model="GigaChat",
        ca_bundle_file="russian_trusted_root_ca_pem.crt",
) as client:
    models = client.get_models()
    for model in models.data:
        print(f"{model.id_} (owned_by={model.owned_by})")

ALLOWED_EXTENSIONS = {
    "guestrag": {".pdf", ".docx", ".txt"},
    "file": {".pdf", ".docx", ".txt"},
}
ext = "truww.pdf".split('.')[-1].lower()
print(ext)
print(ext in ALLOWED_EXTENSIONS.get("guestrag"))
