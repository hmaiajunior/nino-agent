"""Janela de atendimento HUMANO.

O agente responde 24/7; estas funções são usadas apenas para decidir se
um aviso sobre horário de atendimento deve acompanhar uma escalada para
humano (ex.: cliente envia áudio às 23h sábado).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import settings


def _parse_hora(s: str) -> tuple[int, int]:
    h, m = s.strip().split(":")
    return int(h), int(m)


def esta_em_horario_comercial(agora: datetime | None = None) -> bool:
    """True se o momento atual está dentro do horário de atendimento humano."""
    tz = ZoneInfo(settings.HORARIO_ATENDIMENTO_TZ)
    agora = (agora or datetime.now(tz)).astimezone(tz)

    dias_validos = {int(d) for d in settings.HORARIO_ATENDIMENTO_DIAS.split(",") if d.strip()}
    if agora.isoweekday() not in dias_validos:
        return False

    inicio_h, inicio_m = _parse_hora(settings.HORARIO_ATENDIMENTO_INICIO)
    fim_h, fim_m = _parse_hora(settings.HORARIO_ATENDIMENTO_FIM)
    minutos_agora = agora.hour * 60 + agora.minute
    return (inicio_h * 60 + inicio_m) <= minutos_agora < (fim_h * 60 + fim_m)


def aviso_horario_se_fora() -> str:
    """Sufixo a anexar quando uma escalada para humano ocorre fora do expediente.

    Retorna string vazia se está dentro do expediente — para o chamador
    concatenar sem se preocupar.
    """
    if esta_em_horario_comercial():
        return ""
    return (
        f" Nosso atendimento especializado responde em horário comercial "
        f"({settings.HORARIO_ATENDIMENTO})."
    )
