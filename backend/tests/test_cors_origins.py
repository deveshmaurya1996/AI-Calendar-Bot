from app.config import Settings, cors_allow_origins


def test_cors_includes_localhost_and_frontend_public_url() -> None:
    s = Settings(FRONTEND_PUBLIC_URL="https://app.example.com")
    out = cors_allow_origins(s)
    assert "http://localhost:3000" in out
    assert "http://localhost:3001" in out
    assert "https://app.example.com" in out


def test_cors_allow_origins_parses_comma_separated() -> None:
    s = Settings(
        FRONTEND_PUBLIC_URL="http://localhost:3000",
        CORS_ALLOW_ORIGINS="https://preview.vercel.app, https://staging.example.com/",
    )
    out = cors_allow_origins(s)
    assert "https://preview.vercel.app" in out
    assert "https://staging.example.com" in out
