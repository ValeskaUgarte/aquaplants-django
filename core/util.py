import re
import time
from datetime import datetime, timezone


def ahora_ms() -> int:
    return int(time.time() * 1000)


def clave_ms(ms: int) -> str:
    """Clave RTDB ordenable cronológicamente (13 dígitos)."""
    return f"{int(ms):013d}"


def iso_a_ms(texto):
    """ISO 8601 (con Z u offset) -> milisegundos Unix. Lanza ValueError si no es válida."""
    if not isinstance(texto, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:\d{2})", texto):
        raise ValueError("fecha inválida")
    dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def ms_a_iso(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso(ms):
    """ms -> ISO 8601, o None si no hay valor."""
    return ms_a_iso(ms) if ms else None


def distancia_m(lat1, lng1, lat2, lng2):
    """Distancia en metros entre dos coordenadas (fórmula de haversine)."""
    import math
    p1, p2, dp, dl = math.radians(lat1), math.radians(lat2), math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
