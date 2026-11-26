"""Подсчёт статистики матча: K/D, ADR, KAST, HS%, MVP и т.д."""
from __future__ import annotations

from dataclasses import dataclass

from .models import MatchRecord, RoundRecord


@dataclass
class MatchStats:
    rounds: int
    kills: int
    deaths: int
    assists: int
    headshots: int
    damage: int
    mvps: int
    kd: float
    adr: float
    kast: float  # %
    hs_pct: float  # %
    multikills: dict[int, int]  # {2: n, 3: n, 4: n, 5: n}
    rounds_won: int
    rounds_lost: int
    zero_damage_rounds: int
    early_deaths: int  # смерти в первые 20 сек раунда
    survived_rounds: int
    kills_ct: int
    kills_t: int
    deaths_ct: int
    deaths_t: int
    rating: float  # упрощённый рейтинг (по мотивам HLTV 1.0)


EARLY_DEATH_SEC = 20.0


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def compute(match: MatchRecord) -> MatchStats:
    rs: list[RoundRecord] = match.rounds
    n = len(rs)
    kills = sum(r.kills for r in rs)
    deaths = sum(1 for r in rs if r.died)
    assists = sum(r.assists for r in rs)
    hs = sum(r.headshot_kills for r in rs)
    dmg = sum(r.damage for r in rs)

    # match_stats из GSI точнее (учитывает смерти после конца раунда и т.п.)
    if match.total_kills is not None:
        kills = match.total_kills
    if match.total_deaths is not None:
        deaths = match.total_deaths
    if match.total_assists is not None:
        assists = match.total_assists

    mvps = match.mvps or sum(1 for r in rs if r.mvp)
    multi = {k: 0 for k in (2, 3, 4, 5)}
    for r in rs:
        if r.kills >= 2:
            multi[min(r.kills, 5)] += 1

    kd = round(_safe_div(kills, deaths) if deaths else float(kills), 2)
    adr = round(_safe_div(dmg, n), 1)
    kast = round(_safe_div(sum(1 for r in rs if r.kast), n) * 100, 1)
    hs_pct = round(_safe_div(hs, kills) * 100, 1)

    # Упрощённый рейтинг HLTV 1.0: (KillRating + 0.7*SurvivalRating + RMK)/2.7
    kpr = _safe_div(kills, n)
    spr = _safe_div(n - deaths, n)
    rmk = _safe_div(
        sum(1 for r in rs if r.kills == 1)
        + 4 * multi[2] + 9 * multi[3] + 16 * multi[4] + 25 * multi[5],
        n,
    )
    rating = round((kpr / 0.679 + 0.7 * spr / 0.317 + rmk / 1.277) / 2.7, 2) if n else 0.0

    return MatchStats(
        rounds=n,
        kills=kills,
        deaths=deaths,
        assists=assists,
        headshots=hs,
        damage=dmg,
        mvps=mvps,
        kd=kd,
        adr=adr,
        kast=kast,
        hs_pct=hs_pct,
        multikills=multi,
        rounds_won=sum(1 for r in rs if r.won is True),
        rounds_lost=sum(1 for r in rs if r.won is False),
        zero_damage_rounds=sum(1 for r in rs if r.damage == 0),
        early_deaths=sum(
            1 for r in rs if r.died and r.death_time is not None and r.death_time <= EARLY_DEATH_SEC
        ),
        survived_rounds=sum(1 for r in rs if r.survived),
        kills_ct=sum(r.kills for r in rs if r.side == "CT"),
        kills_t=sum(r.kills for r in rs if r.side == "T"),
        deaths_ct=sum(1 for r in rs if r.died and r.side == "CT"),
        deaths_t=sum(1 for r in rs if r.died and r.side == "T"),
        rating=rating,
    )
