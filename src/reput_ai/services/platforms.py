"""Human readable names of the supported review platforms."""

from reput_ai.db.models.branch import PlatformType

PLATFORM_DISPLAY_NAMES: dict[PlatformType, str] = {
    PlatformType.YANDEX: "Яндекс.Карты",
    PlatformType.GIS2: "2ГИС",
    PlatformType.GOOGLE: "Google Maps",
    PlatformType.AVITO: "Авито",
}


def get_platform_display_name(platform_type: PlatformType | str) -> str:
    """Return the public name of a review platform (falls back to the raw value)."""
    if isinstance(platform_type, str):
        try:
            platform_type = PlatformType(platform_type)
        except ValueError:
            return platform_type
    return PLATFORM_DISPLAY_NAMES.get(platform_type, str(platform_type))
