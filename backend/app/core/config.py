from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    ALGORITHM: str = "HS256"
    DATABASE_URL: str
    DAQ_URL: str = "http://daq-dev:8001"
    all_cors_origins: bool = True
    FIRST_SUPERUSER: str = "admin"
    FIRST_SUPERUSER_PASSWORD: str = "admin@1234"
    FIRST_SUPERUSER_EMAIL: str = "admin@example.com"

    class Config:
        env_file = ".env"


config = Settings()
