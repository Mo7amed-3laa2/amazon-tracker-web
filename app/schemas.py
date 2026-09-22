"""Request and response bodies for the JSON API."""

from pydantic import BaseModel, Field, field_validator

from scan import MAX_CHECK_INTERVAL_MINUTES, MIN_CHECK_INTERVAL_MINUTES


class Credentials(BaseModel):
    """Sign-in credentials.

    No minimum on the password: a length rule here would answer 422 to a wrong
    guess, which tells an attacker something the 401 does not, and would lock
    out any account whose password predates the rule. Registration enforces the
    minimum instead — see NewCredentials.
    """

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Username cannot be blank")
        return stripped


class NewCredentials(Credentials):
    password: str = Field(min_length=8, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class Account(BaseModel):
    user_id: int
    username: str
    language: str
    is_admin: bool


class LanguageChange(BaseModel):
    language: str

    @field_validator("language")
    @classmethod
    def _known(cls, value: str) -> str:
        # The bot shipped these two, and the notification copy in
        # reference/scheduler_telegram.py only exists in these two.
        if value not in ("en", "ar"):
            raise ValueError("Language must be 'en' or 'ar'")
        return value


class NewProduct(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    require_amazon_merchant: bool = False

    @field_validator("url")
    @classmethod
    def _looks_like_amazon(cls, value: str) -> str:
        """Reject non-Amazon URLs here rather than after a 30-second scrape.

        Deliberately loose: the scraper handles every regional domain plus the
        amzn.eu / amzn.to / a.co short links, so this only filters out input
        that could not possibly be a product page.
        """
        url = value.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if "amazon." not in url and "amzn." not in url and "a.co/" not in url:
            raise ValueError("Not an Amazon URL")
        return url


class ProductPrefs(BaseModel):
    """A partial update. An omitted field is left alone; an explicit null clears it.

    Pydantic's `exclude_unset` is what separates the two, so the route reads
    model_dump(exclude_unset=True) rather than the attributes.
    """

    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    alert_threshold: float | None = Field(default=None, ge=0)
    require_amazon_merchant: bool | None = None


class IntervalChange(BaseModel):
    check_interval_minutes: int = Field(
        ge=MIN_CHECK_INTERVAL_MINUTES,
        le=MAX_CHECK_INTERVAL_MINUTES,
    )
