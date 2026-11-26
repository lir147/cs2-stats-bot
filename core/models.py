"""Модели данных матча. Только стандартная библиотека."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RoundRecord:
    """Статистика игрока за один раунд."""

    number: int  # номер раунда, с 1
    side: str = ""  # "CT" / "T"
    kills: int = 0
    headshot_kills: int = 0
    assists: int = 0
    damage: int = 0
    died: bool = False
    survived: bool = True
    traded: bool = False  # смерть разменяна тиммейтом (если известно)
    death_time: float | None = None  # секунд от начала раунда (live) до смерти
    mvp: bool = False
    won: bool | None = None  # выиграла ли команда игрока раунд
    win_reason: str = ""  # ct_win_elimination, t_win_bomb, ...
    money_start: int | None = None  # деньги в начале закупки
    money_after_buy: int | None = None  # деньги после закупки (старт раунда)
    equip_value: int | None = None  # стоимость снаряжения на старте раунда
    flashed_on_death: bool = False
    grenades_unused_on_death: int = 0

    @property
    def kast(self) -> bool:
        return self.kills > 0 or self.assists > 0 or self.survived or self.traded

    @property
    def spent(self) -> int | None:
        if self.money_start is None or self.money_after_buy is None:
            return None
        return max(0, self.money_start - self.money_after_buy)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RoundRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class MatchRecord:
    """Матч целиком (с точки зрения одного игрока)."""

    map_name: str = "unknown"
    mode: str = ""
    steamid: str = ""
    player_name: str = ""
    started_at: float = 0.0
    ended_at: float | None = None
    source: str = "gsi"  # gsi / console / json
    score_ct: int = 0
    score_t: int = 0
    final_side: str = ""
    mvps: int = 0
    total_kills: int | None = None  # из match_stats, если есть
    total_deaths: int | None = None
    total_assists: int | None = None
    rounds: list[RoundRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rounds"] = [r.to_dict() for r in self.rounds]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MatchRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data = {k: v for k, v in d.items() if k in known and k != "rounds"}
        m = cls(**data)
        m.rounds = [RoundRecord.from_dict(r) for r in d.get("rounds", [])]
        return m
