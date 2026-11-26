"""Разбор потока Game State Integration (GSI) от клиента CS2.

CS2 шлёт POST с JSON при каждом изменении состояния. Данные о «своём» игроке
приходят только пока он жив: после смерти блок `player` описывает того, за кем
он наблюдает. Поэтому трекер сравнивает `player.steamid` с `provider.steamid`
и считает смерть в том числе по факту переключения на наблюдение.

Трекер не зависит от сети и Telegram — его удобно тестировать.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .models import MatchRecord, RoundRecord

BUY_WINDOW_SEC = 20  # после старта раунда в байзоне ещё можно докупаться


@dataclass
class GsiEvent:
    kind: str  # "round_finished" | "match_finished"
    match: MatchRecord
    round: RoundRecord | None = None


@dataclass
class _Stats:
    kills: int = 0
    assists: int = 0
    deaths: int = 0
    mvps: int = 0

    @classmethod
    def from_payload(cls, ms: dict[str, Any] | None) -> "_Stats | None":
        if not ms:
            return None
        return cls(
            kills=int(ms.get("kills", 0) or 0),
            assists=int(ms.get("assists", 0) or 0),
            deaths=int(ms.get("deaths", 0) or 0),
            mvps=int(ms.get("mvps", 0) or 0),
        )


def _count_grenades(weapons: dict[str, Any] | None) -> int:
    if not weapons:
        return 0
    total = 0
    for w in weapons.values():
        if not isinstance(w, dict) or w.get("type") != "Grenade":
            continue
        # у флешек ammo_reserve может быть 2
        total += max(1, int(w.get("ammo_reserve", 1) or 1))
    return total


@dataclass
class GsiTracker:
    """Состояние одного игрока (одного токена GSI)."""

    match: MatchRecord | None = None
    cur: RoundRecord | None = None
    round_phase: str = ""
    round_live_ts: float | None = None
    baseline: _Stats | None = None  # match_stats на начало текущего раунда
    last_stats: _Stats | None = None
    last_own_state: dict[str, Any] = field(default_factory=dict)
    last_own_weapons: dict[str, Any] = field(default_factory=dict)
    seen_own_this_round: bool = False
    finished_lock: bool = False  # матч закончен, ждём новый
    last_payload_ts: float = 0.0
    pending_finish: tuple[dict[str, Any], dict[str, Any], float] | None = None

    def flush(self) -> list[GsiEvent]:
        """Принудительно завершить матч, если gameover пришёл, а «свой» payload — нет."""
        if self.pending_finish is None or self.match is None:
            self.pending_finish = None
            return []
        mp, rnd, ts = self.pending_finish
        return self._finish_match(mp, rnd, None, ts)

    # ------------------------------------------------------------------ API
    def process(self, payload: dict[str, Any], now: float | None = None) -> list[GsiEvent]:
        provider = payload.get("provider") or {}
        if now is None:
            now = float(provider.get("timestamp") or time.time())
        self.last_payload_ts = now

        mp = payload.get("map")
        if not mp:  # главное меню — матча нет
            return []

        events: list[GsiEvent] = []
        phase = mp.get("phase", "")
        rnd = payload.get("round") or {}
        rphase = rnd.get("phase", "")
        player = payload.get("player") or {}
        own_sid = str(provider.get("steamid", ""))
        own = bool(player) and str(player.get("steamid", "")) == own_sid

        if phase == "warmup":
            self._reset(keep_lock=False)
            return events

        if phase == "gameover":
            if self.match is not None and not self.finished_lock:
                if rnd.get("win_team") and self.cur is not None and self.cur.side:
                    self.cur.won = rnd["win_team"] == self.cur.side
                    self.cur.win_reason = _last_round_reason(mp)
                if own:
                    # итоговые match_stats (MVP последнего раунда и т.п.)
                    events.extend(self._finish_match(mp, rnd, player, now))
                else:
                    # игрок мёртв и смотрит за другим — ждём «свой» payload
                    # (обычно приходит через пару секунд) или flush() по таймауту
                    if self.cur is not None and self.seen_own_this_round:
                        self._mark_death(now)
                    self.pending_finish = (mp, rnd, now)
            return events

        if phase not in ("live", "intermission"):
            return events

        # Новый матч / рестарт
        map_name = mp.get("name", "unknown")
        restarted = (
            self.match is not None
            and int(mp.get("round", 0) or 0) == 0
            and rphase == "freezetime"
            and len(self.match.rounds) >= 2
        )
        if self.match is not None and (self.match.map_name != map_name or restarted):
            self._reset(keep_lock=False)
        if self.match is None:
            self.finished_lock = False
            self.match = MatchRecord(
                map_name=map_name,
                mode=mp.get("mode", ""),
                steamid=own_sid,
                started_at=now,
                source="gsi",
            )

        if own and player.get("name"):
            self.match.player_name = player["name"]

        # Смена фазы раунда
        if rphase != self.round_phase:
            if rphase == "freezetime":
                # закрываем прошлый раунд: в новой закупке игрок снова жив,
                # и match_stats отражают итог предыдущего раунда
                if self.cur is not None:
                    ev = self._finalize_round(player if own else None)
                    if ev:
                        events.append(ev)
                self._start_round()
            elif rphase == "live":
                if self.cur is None:
                    self._start_round()
                self.round_live_ts = now
            elif rphase == "over":
                if self.cur is None:
                    self._start_round()
            self.round_phase = rphase

        if self.cur is None:
            self._start_round()

        if rphase == "over" and rnd.get("win_team") and self.cur.side:
            self.cur.won = rnd["win_team"] == self.cur.side
            self.cur.win_reason = _last_round_reason(mp)

        if own:
            self._update_own(player, rphase, now)
        elif player and self.seen_own_this_round and rphase in ("live", "over"):
            # смотрим за другим игроком → мы мертвы
            self._mark_death(now)

        self.match.score_ct = int((mp.get("team_ct") or {}).get("score", 0) or 0)
        self.match.score_t = int((mp.get("team_t") or {}).get("score", 0) or 0)
        return events

    # ------------------------------------------------------------ helpers
    def _reset(self, keep_lock: bool) -> None:
        self.match = None
        self.cur = None
        self.round_phase = ""
        self.round_live_ts = None
        self.baseline = None
        self.last_stats = None
        self.last_own_state = {}
        self.last_own_weapons = {}
        self.seen_own_this_round = False
        self.pending_finish = None
        if not keep_lock:
            self.finished_lock = False

    def _start_round(self) -> None:
        assert self.match is not None
        self.cur = RoundRecord(number=len(self.match.rounds) + 1)
        self.round_live_ts = None
        self.baseline = self.last_stats
        self.seen_own_this_round = False

    def _update_own(self, player: dict[str, Any], rphase: str, now: float) -> None:
        cur = self.cur
        if cur is None:
            return
        st = player.get("state") or {}
        if player.get("team"):
            cur.side = player["team"]
        self.seen_own_this_round = True

        money = st.get("money")
        equip = st.get("equip_value")
        in_buy_window = rphase == "freezetime" or (
            rphase == "live"
            and self.round_live_ts is not None
            and now - self.round_live_ts <= BUY_WINDOW_SEC
        )
        if money is not None and in_buy_window and not cur.died:
            if cur.money_start is None:
                cur.money_start = int(money)
            cur.money_after_buy = int(money)
            if equip is not None:
                cur.equip_value = int(equip)

        if rphase in ("live", "over"):
            cur.kills = max(cur.kills, int(st.get("round_kills", 0) or 0))
            cur.headshot_kills = max(cur.headshot_kills, int(st.get("round_killhs", 0) or 0))
            cur.damage = max(cur.damage, int(st.get("round_totaldmg", 0) or 0))
            health = st.get("health")
            if health is not None and int(health) <= 0:
                self._mark_death(now)

        stats = _Stats.from_payload(player.get("match_stats"))
        if stats is not None:
            if self.baseline is None:
                self.baseline = stats
            self.last_stats = stats

        if not cur.died and int(st.get("health", 100) or 0) > 0:
            self.last_own_state = dict(st)
            self.last_own_weapons = dict(player.get("weapons") or {})

    def _mark_death(self, now: float) -> None:
        cur = self.cur
        if cur is None or cur.died:
            return
        cur.died = True
        cur.survived = False
        if self.round_live_ts is not None:
            cur.death_time = round(max(0.0, now - self.round_live_ts), 1)
        cur.flashed_on_death = int(self.last_own_state.get("flashed", 0) or 0) > 0
        cur.grenades_unused_on_death = _count_grenades(self.last_own_weapons)

    def _finalize_round(self, own_player: dict[str, Any] | None) -> GsiEvent | None:
        assert self.match is not None
        cur = self.cur
        self.cur = None
        if cur is None or not cur.side:
            return None  # раунд без данных о своём игроке
        now_stats = _Stats.from_payload(own_player.get("match_stats")) if own_player else None
        if now_stats is not None:
            self.last_stats = now_stats
        end = self.last_stats
        base = self.baseline
        if end is not None and base is not None:
            cur.assists = max(0, end.assists - base.assists)
            if end.mvps > base.mvps:
                cur.mvp = True
            if end.deaths > base.deaths and not cur.died:
                cur.died = True
                cur.survived = False
            kills_delta = end.kills - base.kills
            if kills_delta > cur.kills:
                cur.kills = kills_delta
        cur.headshot_kills = min(cur.headshot_kills, cur.kills)
        self.match.rounds.append(cur)
        return GsiEvent("round_finished", self.match, cur)

    def _finish_match(
        self,
        mp: dict[str, Any],
        rnd: dict[str, Any],
        own_player: dict[str, Any] | None,
        now: float,
    ) -> list[GsiEvent]:
        assert self.match is not None
        events: list[GsiEvent] = []
        if self.cur is not None:
            if rnd.get("win_team") and self.cur.side and self.cur.won is None:
                self.cur.won = rnd["win_team"] == self.cur.side
                self.cur.win_reason = _last_round_reason(mp)
            if own_player is not None:
                # обновим урон/фраги, если игрок дожил до конца
                st = own_player.get("state") or {}
                if not self.cur.died:
                    self.cur.kills = max(self.cur.kills, int(st.get("round_kills", 0) or 0))
                    self.cur.damage = max(self.cur.damage, int(st.get("round_totaldmg", 0) or 0))
            ev = self._finalize_round(own_player)
            if ev:
                events.append(ev)
        m = self.match
        m.ended_at = now
        m.score_ct = int((mp.get("team_ct") or {}).get("score", 0) or 0)
        m.score_t = int((mp.get("team_t") or {}).get("score", 0) or 0)
        if m.rounds:
            m.final_side = m.rounds[-1].side
        if self.last_stats is not None:
            m.mvps = self.last_stats.mvps
            m.total_kills = self.last_stats.kills
            m.total_deaths = self.last_stats.deaths
            m.total_assists = self.last_stats.assists
        else:
            m.mvps = sum(1 for r in m.rounds if r.mvp)
        if m.rounds:
            events.append(GsiEvent("match_finished", m))
        self._reset(keep_lock=True)
        self.finished_lock = True
        return events


def _last_round_reason(mp: dict[str, Any]) -> str:
    wins = mp.get("round_wins") or {}
    if not wins:
        return ""
    try:
        key = max(wins, key=lambda k: int(k))
    except ValueError:
        return ""
    return str(wins[key])
