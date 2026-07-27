from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    ALGORITHM: str = "HS256"
    DATABASE_HOST: str = "postgresql"
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "lfnp_daq"
    DATABASE_USER: str
    DATABASE_PASSWORD: str
    DAQ_HOST: str = "daq-dev"
    DAQ_PORT: int = 8001
    all_cors_origins: bool = True
    FIRST_SUPERUSER: str = "admin"
    FIRST_SUPERUSER_PASSWORD: str = "admin@1234"
    FIRST_SUPERUSER_EMAIL: str = "admin@example.com"

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+psycopg://{self.DATABASE_USER}:{self.DATABASE_PASSWORD}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"

    @property
    def DAQ_URL(self) -> str:
        return f"http://{self.DAQ_HOST}:{self.DAQ_PORT}"

    class Config:
        env_file = ".env"


config = Settings()
