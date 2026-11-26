"""Разбор ошибок (позиционка, тайминги, урон, экономика, гранаты) и советы.

Правила эвристические: GSI отдаёт только данные о своём игроке (без координат
и без информации о врагах), поэтому выводы делаются по косвенным признакам —
когда умер, сколько успел нанести, сколько потратил и т.д.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import MatchRecord, RoundRecord
from .stats import EARLY_DEATH_SEC, MatchStats, compute

CATEGORIES = {
    "positioning": "Позиционка",
    "timing": "Тайминги",
    "damage": "Урон",
    "economy": "Экономика",
    "utility": "Гранаты",
}

FULL_BUY = 4000  # стоимость снаряжения, начиная с которой считаем раунд фулл-баем


@dataclass
class Finding:
    category: str
    severity: float  # 0..1 — насколько проблема выражена
    text: str
    tip: str
    rounds: list[int] = field(default_factory=list)

    @property
    def title(self) -> str:
        return CATEGORIES.get(self.category, self.category)


@dataclass
class Analysis:
    stats: MatchStats
    findings: list[Finding]
    tips: list[str]
    strengths: list[str]


def _nums(rs: list[RoundRecord]) -> list[int]:
    return [r.number for r in rs]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def analyze(match: MatchRecord) -> Analysis:
    st = compute(match)
    rs = match.rounds
    n = max(1, len(rs))
    deaths = [r for r in rs if r.died]
    nd = max(1, len(deaths))
    f: list[Finding] = []

    # ----------------------------------------------------------- позиционка
    early_dry = [
        r for r in deaths
        if r.death_time is not None and r.death_time <= EARLY_DEATH_SEC and r.kills == 0
    ]
    if len(early_dry) >= 2 and len(early_dry) / nd >= 0.25:
        f.append(Finding(
            "positioning", _clamp(len(early_dry) / nd * 1.5),
            f"{len(early_dry)} раз(а) погиб в первые {int(EARLY_DEATH_SEC)} сек раунда без фрага — "
            "тебя ловят на стандартных позициях или на первом пике.",
            "Меняй позицию каждые 2–3 раунда и не выходи первым в открытую — играй от тиммейта, "
            "чтобы тебя могли разменять.",
            _nums(early_dry),
        ))

    dry_deaths = [r for r in deaths if r.damage == 0]
    if len(dry_deaths) >= 3 and len(dry_deaths) / nd >= 0.3:
        f.append(Finding(
            "positioning", _clamp(len(dry_deaths) / nd),
            f"В {len(dry_deaths)} раундах умер, не нанеся ни единицы урона — "
            "враг видит тебя первым.",
            "Перед выходом на угол заранее держи прицел на уровне головы и используй "
            "jiggle-пик, чтобы собрать информацию, а не отдать фраг.",
            _nums(dry_deaths),
        ))

    flashed = [r for r in deaths if r.flashed_on_death]
    if len(flashed) >= 2:
        f.append(Finding(
            "positioning", _clamp(len(flashed) / nd * 2),
            f"{len(flashed)} раз(а) погиб ослеплённым.",
            "Услышал флешку — отворачивайся и отходи за укрытие, а не держи угол вслепую.",
            _nums(flashed),
        ))

    ct_r = [r for r in rs if r.side == "CT"]
    t_r = [r for r in rs if r.side == "T"]
    if len(ct_r) >= 5 and len(t_r) >= 5:
        ct_dr = sum(r.died for r in ct_r) / len(ct_r)
        t_dr = sum(r.died for r in t_r) / len(t_r)
        if ct_dr - t_dr >= 0.25:
            f.append(Finding(
                "positioning", _clamp((ct_dr - t_dr) * 1.5),
                f"За CT умираешь в {ct_dr:.0%} раундов против {t_dr:.0%} за T — "
                "проблема в обороне точки.",
                "За CT держи позицию с отходом и кроссфаер с напарником; после первого контакта "
                "отходи и жди ретейк, а не дуэль 1 на 1.",
            ))
        elif t_dr - ct_dr >= 0.25:
            f.append(Finding(
                "positioning", _clamp((t_dr - ct_dr) * 1.5),
                f"За T умираешь в {t_dr:.0%} раундов против {ct_dr:.0%} за CT — "
                "заходы на точку проваливаются.",
                "За T заходи на точку сразу за энтри-фраггером с флешкой, а не отдельно от команды.",
            ))

    # ------------------------------------------------------------- тайминги
    times = [r.death_time for r in deaths if r.death_time is not None]
    if len(times) >= 4:
        avg = sum(times) / len(times)
        if avg < 35:
            f.append(Finding(
                "timing", _clamp((35 - avg) / 25),
                f"Средняя секунда смерти — {avg:.0f}-я: слишком рано ищешь дуэли.",
                "Не торопись: первые 20–30 секунд собирай информацию и жди ошибку соперника, "
                "особенно за CT.",
            ))
        late_lost = [
            r for r in deaths
            if r.death_time is not None and r.death_time >= 90 and r.won is False and r.kills == 0
        ]
        if len(late_lost) >= 3:
            f.append(Finding(
                "timing", _clamp(len(late_lost) / nd * 1.5),
                f"{len(late_lost)} раз(а) погиб в конце проигранного раунда без фрага — "
                "клатчи и ретейки в одиночку.",
                "Если раунд уже не вытащить (1 против 3+, мало времени), сохраняй оружие "
                "для следующего раунда.",
                _nums(late_lost),
            ))

    # ------------------------------------------------------------------ урон
    if len(rs) >= 5 and st.adr < 65:
        f.append(Finding(
            "damage", _clamp((65 - st.adr) / 40),
            f"ADR {st.adr} — мало урона за раунд (ориентир для среднего уровня: 70–80).",
            "Каждый раунд старайся нанести урон хотя бы через гранату: HE и молотов в "
            "стандартные позиции дают по 20–40 урона без риска.",
        ))
    if st.zero_damage_rounds >= 3 and st.zero_damage_rounds / n >= 0.3:
        zr = [r for r in rs if r.damage == 0]
        f.append(Finding(
            "damage", _clamp(st.zero_damage_rounds / n),
            f"В {st.zero_damage_rounds} из {len(rs)} раундов ноль урона — тебя нет в раунде.",
            "Занимай позиции, где будет контакт, а не дальний угол без действия.",
            _nums(zr),
        ))
    no_finish = [r for r in rs if r.damage >= 100 and r.kills == 0]
    if len(no_finish) >= 2:
        f.append(Finding(
            "damage", _clamp(len(no_finish) / n * 3),
            f"В {len(no_finish)} раундах ≥100 урона и ни одного фрага — не добиваешь.",
            "После попадания не уходи сразу: добей соперника с сохранённой позиции "
            "или скинь инфу по HP тиммейту.",
            _nums(no_finish),
        ))
    if st.kills >= 6 and st.hs_pct < 30:
        f.append(Finding(
            "damage", _clamp((30 - st.hs_pct) / 30),
            f"HS% {st.hs_pct} — мало попаданий в голову.",
            "Следи за прицелом на уровне головы и тренируй его 10 минут перед игрой "
            "(Aim Botz / deathmatch только на хедшоты).",
        ))

    # ------------------------------------------------------------- экономика
    with_money = [r for r in rs if r.money_start is not None and r.money_after_buy is not None]
    hoarded = [
        r for r in with_money
        if r.money_after_buy >= 5000 and (r.equip_value or 0) < FULL_BUY
    ]
    if len(hoarded) >= 2:
        f.append(Finding(
            "economy", _clamp(len(hoarded) / n * 3),
            f"В {len(hoarded)} раундах при деньгах ≥$5000 играл без полной закупки.",
            "Если у тебя есть $5000+, закупайся полностью (броня, винтовка, гранаты) "
            "или купи оружие тиммейту.",
            _nums(hoarded),
        ))
    by_num = {r.number: r for r in rs}
    bad_force = []
    for r in with_money:
        spent = r.spent or 0
        nxt = by_num.get(r.number + 1)
        if (
            r.money_start < 3500 and spent >= 2000 and r.won is False
            and nxt is not None and nxt.money_start is not None and nxt.money_start < 2500
            and r.number not in (1, 13)  # пистолетки не считаем
        ):
            bad_force.append(r)
    if len(bad_force) >= 2:
        f.append(Finding(
            "economy", _clamp(len(bad_force) / n * 4),
            f"{len(bad_force)} неудачных форса подряд оставили тебя без денег на следующий раунд.",
            "Форси только вместе с командой; если команда экономит — экономь тоже, "
            "чтобы в следующем раунде у всех был фулл-бай.",
            _nums(bad_force),
        ))
    lost_gear = [
        r for r in deaths
        if (r.equip_value or 0) >= FULL_BUY and r.kills == 0 and r.damage < 50
    ]
    if len(lost_gear) >= 3:
        f.append(Finding(
            "economy", _clamp(len(lost_gear) / nd * 1.5),
            f"{len(lost_gear)} раз(а) потерял фулл-бай без фрага и почти без урона.",
            "С дорогим оружием не лезь первым в рискованные пики — отдавай этот выход "
            "тиммейту с дешёвым оружием.",
            _nums(lost_gear),
        ))

    # ---------------------------------------------------------------- гранаты
    wasted = [r for r in deaths if r.grenades_unused_on_death > 0]
    total_wasted = sum(r.grenades_unused_on_death for r in wasted)
    if total_wasted >= 3:
        f.append(Finding(
            "utility", _clamp(total_wasted / (n * 1.5)),
            f"Неиспользованных гранат на момент смерти: {total_wasted} — они «ушли в могилу».",
            "Используй гранаты до дуэли: флешка перед пиком, молотов на стандартную позицию.",
            _nums(wasted),
        ))

    f.sort(key=lambda x: x.severity, reverse=True)
    tips = _pick_tips(f, st)
    return Analysis(stats=st, findings=f, tips=tips, strengths=_strengths(st))


GENERIC_TIPS = [
    ("adr", "Подними ADR: в каждом раунде нанеси урон через HE или молотов ещё до дуэлей."),
    ("kast", "Работай на KAST: играй ближе к тиммейтам, чтобы тебя разменивали, а ты разменивал их."),
    ("hs", "Перед игрой 10 минут deathmatch только на хедшоты — для прицела на уровне головы."),
    ("default", "Пересмотри демку 2–3 проигранных раундов и найди момент, где решение было неверным."),
    ("default2", "Перед каждым раундом решай, какую роль играешь (энтри, поддержка, люркер), и придерживайся её."),
    ("default3", "Выучи 2–3 раскидки гранат на этой карте для своей любимой позиции — это бесплатное преимущество."),
]


def _pick_tips(findings: list[Finding], st: MatchStats) -> list[str]:
    tips: list[str] = []
    used_cat: set[str] = set()
    # сначала по одному совету из разных категорий
    for fd in findings:
        if fd.category not in used_cat and fd.tip not in tips:
            tips.append(fd.tip)
            used_cat.add(fd.category)
        if len(tips) == 3:
            return tips
    for fd in findings:
        if fd.tip not in tips:
            tips.append(fd.tip)
        if len(tips) == 3:
            return tips
    order = []
    if st.adr < 80:
        order.append("adr")
    if st.kast < 70:
        order.append("kast")
    if st.hs_pct < 45:
        order.append("hs")
    order += ["default", "default2", "default3"]
    gen = dict(GENERIC_TIPS)
    for key in order:
        t = gen[key]
        if t not in tips:
            tips.append(t)
        if len(tips) == 3:
            break
    return tips


def _strengths(st: MatchStats) -> list[str]:
    s = []
    if st.kd >= 1.2:
        s.append(f"K/D {st.kd} — выигрываешь больше дуэлей, чем проигрываешь")
    if st.adr >= 85:
        s.append(f"ADR {st.adr} — высокий урон")
    if st.kast >= 75:
        s.append(f"KAST {st.kast}% — полезен в большинстве раундов")
    if st.hs_pct >= 50 and st.kills >= 5:
        s.append(f"HS% {st.hs_pct} — отличный прицел")
    if st.multikills.get(3, 0) + st.multikills.get(4, 0) + st.multikills.get(5, 0) >= 2:
        s.append("несколько раундов с 3+ фрагами")
    if st.mvps >= 3:
        s.append(f"{st.mvps} MVP")
    return s
