"""Генератор правдоподобного потока GSI-сообщений для тестов и демо."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SID = "76561198000000001"
OTHER = "76561198000000002"


@dataclass
class RoundPlan:
    side: str = "CT"
    kills: int = 0
    hs: int = 0
    damage: int = 0
    assists: int = 0
    die_at: float | None = None  # секунда смерти, None — выжил
    win: bool = True
    mvp: bool = False
    money: int = 4000
    spend: int = 3500
    flashed_on_death: bool = False
    grenades: int = 0


def _payload(ts, phase, rphase, rnd, score, player, win_team=None, round_wins=None, token="tok"):
    p: dict[str, Any] = {
        "provider": {"name": "Counter-Strike 2", "appid": 730, "steamid": SID, "timestamp": ts},
        "map": {
            "mode": "competitive", "name": "de_mirage", "phase": phase, "round": rnd,
            "team_ct": {"score": score[0]}, "team_t": {"score": score[1]},
            "round_wins": round_wins or {},
        },
        "round": {"phase": rphase},
        "player": player,
        "auth": {"token": token},
    }
    if win_team:
        p["round"]["win_team"] = win_team
    return p


def _me(team, health, money, equip, rk, rhs, dmg, ms, flashed=0, grenades=0):
    weapons = {"weapon_0": {"name": "weapon_knife", "type": "Knife"},
               "weapon_1": {"name": "weapon_ak47", "type": "Rifle"}}
    for i in range(grenades):
        weapons[f"weapon_{2 + i}"] = {"name": "weapon_flashbang", "type": "Grenade", "ammo_reserve": 1}
    return {
        "steamid": SID, "name": "lir147", "team": team, "activity": "playing",
        "match_stats": dict(ms),
        "state": {"health": health, "armor": 100, "helmet": True, "flashed": flashed,
                  "money": money, "round_kills": rk, "round_killhs": rhs,
                  "round_totaldmg": dmg, "equip_value": equip},
        "weapons": weapons,
    }


def _other(team):
    return {"steamid": OTHER, "name": "mate", "team": team, "activity": "playing",
            "state": {"health": 80, "money": 1000, "round_kills": 0, "round_killhs": 0,
                      "round_totaldmg": 0, "equip_value": 3000}}


def simulate(plans: list[RoundPlan], token: str = "tok") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ts = 1_700_000_000
    ms = {"kills": 0, "assists": 0, "deaths": 0, "mvps": 0, "score": 0}
    mine_theirs = [0, 0]  # счёт моей команды и соперников (в GSI он «переезжает» со сторонами)
    score = [0, 0]
    wins: dict[str, str] = {}
    # разминка
    out.append(_payload(ts, "warmup", "freezetime", 0, score,
                        _me("CT", 100, 800, 200, 0, 0, 0, ms), token=token))
    for i, rp in enumerate(plans):
        team = rp.side
        score = mine_theirs[:] if team == "CT" else mine_theirs[::-1]
        ts += 5
        out.append(_payload(ts, "live", "freezetime", i, score,
                            _me(team, 100, rp.money, 800, 0, 0, 0, ms), round_wins=wins, token=token))
        ts += 10
        after = rp.money - rp.spend
        out.append(_payload(ts, "live", "freezetime", i, score,
                            _me(team, 100, after, rp.spend + 200, 0, 0, 0, ms,
                                grenades=rp.grenades), round_wins=wins, token=token))
        ts += 5
        live_ts = ts
        out.append(_payload(ts, "live", "live", i, score,
                            _me(team, 100, after, rp.spend + 200, 0, 0, 0, ms,
                                grenades=rp.grenades), round_wins=wins, token=token))
        # середина раунда — фраги и урон
        mid = live_ts + (rp.die_at - 1 if rp.die_at else 40)
        out.append(_payload(mid, "live", "live", i, score,
                            _me(team, 60, after, rp.spend + 200, rp.kills, rp.hs, rp.damage, ms,
                                flashed=200 if rp.flashed_on_death else 0, grenades=rp.grenades),
                            round_wins=wins, token=token))
        if rp.die_at is not None:
            out.append(_payload(live_ts + rp.die_at, "live", "live", i, score,
                                _me(team, 0, after, 0, rp.kills, rp.hs, rp.damage, ms),
                                round_wins=wins, token=token))
            out.append(_payload(live_ts + rp.die_at + 2, "live", "live", i, score,
                                _other(team), round_wins=wins, token=token))
        # итог раунда
        ms["kills"] += rp.kills
        ms["assists"] += rp.assists
        ms["deaths"] += 1 if rp.die_at is not None else 0
        ms["mvps"] += 1 if rp.mvp else 0
        win_team = team if rp.win else ("T" if team == "CT" else "CT")
        mine_theirs[0 if rp.win else 1] += 1
        score = mine_theirs[:] if team == "CT" else mine_theirs[::-1]
        wins[str(i + 1)] = "ct_win_elimination" if win_team == "CT" else "t_win_bomb"
        ts = live_ts + 100
        over_player = _other(team) if rp.die_at is not None else _me(
            team, 60, after, rp.spend + 200, rp.kills, rp.hs, rp.damage,
            {**ms, "mvps": ms["mvps"] - (1 if rp.mvp else 0)})
        last = i == len(plans) - 1
        out.append(_payload(ts, "gameover" if last else "live", "over", i + 1, score, over_player,
                            win_team=win_team, round_wins=wins, token=token))
        if last:
            out.append(_payload(ts + 3, "gameover", "over", i + 1, score,
                                _me(team, 100, after, 0, 0, 0, 0, ms),
                                win_team=win_team, round_wins=wins, token=token))
    return out
