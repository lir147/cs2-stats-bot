"""Форматирование отчётов для Telegram (parse_mode=HTML)."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from .analysis import Analysis, analyze
from .models import MatchRecord

_REASONS = {
    "ct_win_elimination": "все T убиты",
    "t_win_elimination": "все CT убиты",
    "ct_win_defuse": "бомба разминирована",
    "t_win_bomb": "бомба взорвана",
    "ct_win_time": "вышло время",
    "t_win_time": "вышло время",
    "ct_win_rescue": "заложники спасены",
}


def _map_title(name: str) -> str:
    return name.replace("de_", "").replace("_", " ").title() if name else "—"


def _result_line(m: MatchRecord) -> str:
    side = m.final_side
    if side in ("CT", "T"):
        mine = m.score_ct if side == "CT" else m.score_t
        theirs = m.score_t if side == "CT" else m.score_ct
        res = "Победа" if mine > theirs else "Поражение" if mine < theirs else "Ничья"
        return f"{res} <b>{mine}:{theirs}</b>"
    if m.score_ct or m.score_t:
        return f"CT {m.score_ct} : {m.score_t} T"
    return ""


def stat_report(m: MatchRecord, a: Analysis | None = None) -> str:
    a = a or analyze(m)
    s = a.stats
    when = ""
    if m.ended_at:
        try:
            when = datetime.fromtimestamp(m.ended_at, tz=timezone.utc).strftime("%d.%m %H:%M UTC")
        except (OverflowError, OSError, ValueError):
            when = ""
    head = f"🎮 <b>{escape(_map_title(m.map_name))}</b>"
    rl = _result_line(m)
    if rl:
        head += f" — {rl}"
    lines = [head]
    sub = " · ".join(x for x in (escape(m.player_name), when, f"{s.rounds} раундов") if x)
    lines.append(f"<i>{sub}</i>")
    if m.source == "console":
        lines.append("<i>⚠️ Импорт из консоли: фраги и смерти определены по урону ≥100, "
                     "без экономики и таймингов.</i>")
    lines += [
        "",
        "📊 <b>Статистика</b>",
        f"K / D / A: <b>{s.kills} / {s.deaths} / {s.assists}</b>   K/D: <b>{s.kd}</b>",
        f"ADR: <b>{s.adr}</b>   KAST: <b>{s.kast}%</b>   HS: <b>{s.hs_pct}%</b>",
        f"MVP: <b>{s.mvps}</b>   Рейтинг*: <b>{s.rating}</b>",
    ]
    mk = ", ".join(f"{k}K×{v}" for k, v in s.multikills.items() if v)
    if mk:
        lines.append(f"Мультикиллы: {mk}")
    if s.kills_ct + s.kills_t + s.deaths_ct + s.deaths_t:
        lines.append(f"За CT: {s.kills_ct}/{s.deaths_ct}   За T: {s.kills_t}/{s.deaths_t} (K/D)")

    if a.strengths:
        lines += ["", "💪 <b>Сильные стороны</b>"]
        lines += [f"• {escape(x)}" for x in a.strengths]

    lines += ["", "🔍 <b>Разбор ошибок</b>"]
    if a.findings:
        for fd in a.findings[:6]:
            rr = ""
            if fd.rounds:
                shown = ", ".join(map(str, fd.rounds[:8]))
                more = "…" if len(fd.rounds) > 8 else ""
                rr = f" <i>(раунды {shown}{more})</i>"
            lines.append(f"• <b>{fd.title}:</b> {escape(fd.text)}{rr}")
    else:
        lines.append("• Явных системных ошибок не найдено — хорошая игра 👍")

    lines += ["", "🎯 <b>3 совета на следующую игру</b>"]
    lines += [f"{i}. {escape(t)}" for i, t in enumerate(a.tips, 1)]
    lines += ["", "<i>* упрощённый рейтинг по формуле HLTV 1.0. Разбивка по раундам — /rounds</i>"]
    return "\n".join(lines)


def rounds_report(m: MatchRecord) -> str:
    lines = [f"🧾 <b>По раундам — {escape(_map_title(m.map_name))}</b>", "<pre>"]
    lines.append(" #  Ст  Рез  K HS  A  Урон Смерть  $старт")
    for r in m.rounds:
        res = "W" if r.won is True else "L" if r.won is False else "-"
        if r.died:
            death = f"{r.death_time:>4.0f}с" if r.death_time is not None else "  да "
        else:
            death = "  —  "
        money = f"{r.money_start:>6}" if r.money_start is not None else "     —"
        mvp = "★" if r.mvp else " "
        lines.append(
            f"{r.number:>2}{mvp}{(r.side or '-'):>3}  {res:^3} {r.kills:>1} {r.headshot_kills:>2} "
            f"{r.assists:>2} {r.damage:>5} {death} {money}"
        )
    lines.append("</pre>")
    lines.append("<i>★ — MVP раунда, «Смерть» — секунда раунда, на которой погиб.</i>")
    return "\n".join(lines)


def short_line(m: MatchRecord) -> str:
    a = analyze(m)
    s = a.stats
    rl = _result_line(m)
    return (f"{escape(_map_title(m.map_name))} {rl} — {s.kills}/{s.deaths}/{s.assists}, "
            f"ADR {s.adr}, KAST {s.kast}%")
