import os
from dotenv import load_dotenv

load_dotenv()


def _b(name: str) -> bool:
    return os.getenv(name, "").strip() not in ("", "0", "false")


class Settings:
    port = int(os.getenv("PORT", "8080"))
    auth_password = os.getenv("AUTH_PASSWORD", "")
    jwt_secret = os.getenv("JWT_SECRET", "dev-insecure-secret")
    token_ttl_s = 7 * 24 * 3600

    # источники
    vk_token = os.getenv("VK_TOKEN", "").strip()
    sc_token = os.getenv("SOUNDCLOUD_OAUTH_TOKEN", "").strip()
    sc_client_id = os.getenv("SOUNDCLOUD_CLIENT_ID", "").strip()
    spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    spotify_redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8600/callback")
    # долгоживущий refresh-токен: один раз получили локально (/auth/spotify/start),
    # вписали сюда — и вход больше не нужен (важно для облака без диска)
    spotify_refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN", "").strip()

    db_path = os.getenv("DB_PATH", "data.db")
    media_cache_dir = os.getenv("MEDIA_CACHE_DIR", "./cache/media")
    media_cache_limit_mb = int(os.getenv("MEDIA_CACHE_LIMIT_MB", "10240"))


settings = Settings()
