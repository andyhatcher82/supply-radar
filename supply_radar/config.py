"""Runtime configuration.

Secrets come from the environment. On Cloud Run they are injected from Secret
Manager; locally they come from a .env file that is never committed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
STATIC_DIR = REPO_ROOT / "static"

# Working artefacts: raw sweeps, the site cache, intermediate JSON. Large,
# regenerable, and deliberately ignored by both git and Docker.
DATA_DIR = REPO_ROOT / "data"

# The PUBLISHED artefact the deployed app serves. Small, committed, and copied
# into the image. Kept separate from DATA_DIR because `data/` is ignored by
# both .gitignore and .dockerignore, so a snapshot living there would be
# missing from the container and the demo would show an empty console.
SNAPSHOT_DIR = REPO_ROOT / "snapshot"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_version: str = "dev"
    environment: str = "local"

    # Secrets
    anthropic_api_key: str = ""
    google_maps_api_key: str = ""
    search_api_key: str = ""

    # One code for two jobs: it opens the site, and it authorises anything
    # billable. Separating them was considered and rejected. The panel is meant
    # to drive the live tool, and a second code at the moment they press Run
    # is friction placed exactly where the demo needs none. Spending stays
    # capped by the 24-cell live limit rather than by a second secret.
    access_code: str = ""

    # Signs the access cookie. Any non-empty value works; it only has to be
    # stable across instances and unknown to the client, so that possessing a
    # cookie cannot be turned back into possessing the code.
    gate_secret: str = "supply-radar-gate"

    # Key for the admin surface. It CAN differ from access_code and originally
    # did, on the reasoning that authorising a sweep and changing what everyone
    # else may sweep are different privileges. For the demo both are set to the
    # same value, so anyone who can open the console can also change these
    # settings, and the Admin page says so rather than implying a separation
    # that is not there.
    #
    # The real answer is neither one code nor two: it is SSO, with the
    # permission read from the person rather than from a code they were told.
    admin_code: str = ""

    # BigQuery — published snapshot store
    bq_project: str = ""
    bq_dataset: str = "supply_radar"

    # Cost guards. Every one of these exists because a public URL with an API
    # key behind it is an invitation, and because a runaway sweep is the most
    # likely way to burn the build budget by accident.
    max_cells_per_run: int = 400
    max_subdivision_depth: int = 4
    daily_spend_cap_gbp: float = 25.0

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def places_enabled(self) -> bool:
        return bool(self.google_maps_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
