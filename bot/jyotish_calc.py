"""Vedic Astrology (Jyotish) Calculation Engine v1.0
====================================================

Реальный астрономический движок расчёта Джйотиш (Ведической Астрологии).
Использует планетные позиции из hd_calc.py (Swiss Ephemeris или fallback)
и конвертирует их в сидерические (ведические) координаты.

Расчёты:
  - Лахири Аянамша (тропическая → сидерическая конвертация)
  - Накшатры (27 лунных стоянок) с пада, управителем, божеством и т.д.
  - Вимшоттари Даша (9 планетных периодов с реальными датами)
  - Атма-карака (планета души — максимальный градус в знаке)
  - Навамша (D9) — карта души
  - Панчанг (5 элементов ведического дня)
  - Мастер-функция + форматирование контекста для ИИ

Автор: nastya-bot engine
"""

import math
import logging
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta, timezone

from bot.hd_calc import (
    calc_all_planets,
    _datetime_to_jd,
    _parse_birth_time,
    _get_timezone_offset,
    longitude_to_gate_line,
    PLANET_NAMES_RU,
)

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
#  КОНСТАНТЫ — ЗНАКИ ЗОДИАКА
# ════════════════════════════════════════════════════════════════

SIGNS_RU: List[str] = [
    'Овен', 'Телец', 'Близнецы', 'Рак',
    'Лев', 'Дева', 'Весы', 'Скорпион',
    'Стрелец', 'Козерог', 'Водолей', 'Рыбы',
]

SIGNS_EN: List[str] = [
    'Aries', 'Taurus', 'Gemini', 'Cancer',
    'Leo', 'Virgo', 'Libra', 'Scorpio',
    'Sagittarius', 'Capricorn', 'Aquarius', 'Pisces',
]

SIGNS_SANSKRIT: List[str] = [
    'Меша', 'Вришабха', 'Митхуна', 'Карка',
    'Симха', 'Канья', 'Тула', 'Вришчика',
    'Дхану', 'Макара', 'Кумбха', 'Мина',
]

# Планеты, используемые в Джйотиш (отличается от западной астрологии)
JYOTISH_PLANETS: List[str] = [
    'sun', 'moon', 'mercury', 'venus', 'mars',
    'jupiter', 'saturn', 'north_node',
]

JYOTISH_PLANET_NAMES_RU: Dict[str, str] = {
    'sun':         'Сурья (Солнце)',
    'moon':        'Чандра (Луна)',
    'mercury':     'Буддха (Меркурий)',
    'venus':       'Шукра (Венера)',
    'mars':        'Мангал (Марс)',
    'jupiter':     'Гуру (Юпитер)',
    'saturn':      'Шани (Сатурн)',
    'north_node':  'Раху',
    'south_node':  'Кету',
}


# ════════════════════════════════════════════════════════════════
#  1. ЛАХИРИ АЯНАМША
# ════════════════════════════════════════════════════════════════

def calculate_lahiri_ayanamsa(year: float) -> float:
    """Рассчитать Лахири Аянамшу для заданного года.

    Аянамша — разница между тропической и сидерической системами координат.
    Лахири (Chitrapaksha) — стандарт в Индии, утверждён правительством.

    Формула: Ayanamsa = 23°51'11" + 50".2735 * (year - 1900)
           + поправки за вековые изменения.

    Более точная формула основана на юлианских столетиях:
    T = (jd - 2451545.0) / 36525.0
    Ayanamsa ≈ 23.85305556 + 0.01395556 * T + 2.0e-7 * T²

    Возвращает аянамшу в градусах.
    """
    # Юлианское столетие от J2000.0
    # Приближённо: 2000.0 → JD 2451545.0
    # Для данного года считаем средний JD
    year_frac = year
    T = (year_frac - 2000.0) / 100.0

    # Лахири аянамша (стандартная аппроксимация)
    # Базовое значение на J2000.0: ~23°51'11" = 23.853°
    # Прецессия: ~50.27"/год = 0.01396°/год
    ayanamsa = 23.85305556 + 0.01395556 * (year_frac - 2000.0) + 2.0e-7 * T * T
    return ayanamsa


def calculate_lahiri_ayanamsa_jd(jd: float) -> float:
    """Рассчитать Лахири Аянамшу по Юлианскому дню.

    Более точный метод, используемый Swiss Ephemeris.
    """
    T = (jd - 2451545.0) / 36525.0
    # Лахири аянамша на J2000.0 ≈ 23.85305556°
    # Прецессия ≈ 50.29"/год
    ayanamsa = 23.85305556 + 0.01395556 * T * 100.0 + 2.0e-7 * T * T * 10000.0
    return ayanamsa


def tropical_to_sidereal(tropical_longitude: float, year: float) -> float:
    """Конвертировать тропическую долготу в сидерическую (Ведическую).

    Args:
        tropical_longitude: Долгота в тропической системе [0, 360)
        year: Год (может быть дробным, например 2025.4)

    Returns:
        Сидерическая долгота [0, 360)
    """
    ayanamsa = calculate_lahiri_ayanamsa(year)
    sidereal = (tropical_longitude - ayanamsa) % 360.0
    if sidereal < 0:
        sidereal += 360.0
    return sidereal


def tropical_to_sidereal_jd(tropical_longitude: float, jd: float) -> float:
    """Конвертировать тропическую долготу в сидерическую по Юлианскому дню."""
    ayanamsa = calculate_lahiri_ayanamsa_jd(jd)
    sidereal = (tropical_longitude - ayanamsa) % 360.0
    if sidereal < 0:
        sidereal += 360.0
    return sidereal


# ════════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ════════════════════════════════════════════════════════════════

def _deg_norm(d: float) -> float:
    """Нормализовать угол в [0, 360)."""
    return d % 360.0


def longitude_to_sign_degree(lon: float) -> Tuple[int, int, float, str]:
    """Разбить сидерическую долготу на знак, градус в знаке, минуты, название знака.

    Returns:
        (sign_index, degrees_in_sign, minutes_fraction, sign_name_ru)
    """
    lon = _deg_norm(lon)
    sign_idx = int(lon / 30.0)
    if sign_idx >= 12:
        sign_idx = 11
    deg_in_sign = lon - sign_idx * 30.0
    minutes_frac = (deg_in_sign - int(deg_in_sign)) * 60.0
    return sign_idx, int(deg_in_sign), minutes_frac, SIGNS_RU[sign_idx]


def longitude_to_dms(lon: float) -> str:
    """Форматировать долготу как 'Знак °′″'."""
    sign_idx, deg, min_frac, sign_name = longitude_to_sign_degree(lon)
    minutes = int(min_frac)
    seconds = int((min_frac - minutes) * 60)
    return f"{sign_name} {deg}°{minutes:02d}'{seconds:02d}\""


# ════════════════════════════════════════════════════════════════
#  2. НАКШАТРЫ — 27 ЛУННЫХ СТОЯНОК
# ════════════════════════════════════════════════════════════════

# Каждая накшатра = 13°20' = 13.3333°
NAKSHATRA_SIZE = 360.0 / 27.0  # ≈ 13.3333°

# Данные 27 накшатр: (санскрит, русский, управитель, божество, символ, гуна, животное, значение)
NAKSHATRA_DATA: List[Dict[str, str]] = [
    {
        "sanskrit": "Ashwini",
        "ru": "Ашвини",
        "ruler": "Кету",
        "deity": "Ашвины (Божественные целители)",
        "symbol": "Голова лошади",
        "guna": "Раджас",
        "animal": "Лошадь",
        "meaning": "Целительство, быстрота, инициация",
    },
    {
        "sanskrit": "Bharani",
        "ru": "Бхарани",
        "ruler": "Венера",
        "deity": "Яма (Бог смерти)",
        "symbol": "Йони (Женский орган)",
        "guna": "Раджас",
        "animal": "Слон",
        "meaning": "Трансформация, очищение, рождение",
    },
    {
        "sanskrit": "Krittika",
        "ru": "Криттика",
        "ruler": "Солнце",
        "deity": "Агни (Бог огня)",
        "symbol": "Острие / Нож",
        "guna": "Раджас",
        "animal": "Овца",
        "meaning": "Острота, отсечение, очищение огнём",
    },
    {
        "sanskrit": "Rohini",
        "ru": "Рохини",
        "ruler": "Луна",
        "deity": "Брахма (Творец)",
        "symbol": "Повозка / Колесница",
        "guna": "Раджас",
        "animal": "Змея",
        "meaning": "Рост, плодородие, творчество",
    },
    {
        "sanskrit": "Mrigashira",
        "ru": "Мригаширша",
        "ruler": "Марс",
        "deity": "Сома (Бог Луны/нектара)",
        "symbol": "Голова оленя",
        "guna": "Саттва",
        "animal": "Змея",
        "meaning": "Поиск, любопытство, нежность",
    },
    {
        "sanskrit": "Ardra",
        "ru": "Ардра",
        "ruler": "Раху",
        "deity": "Рудра (Бог бури)",
        "symbol": "Слеза / Капля",
        "guna": "Раджас",
        "animal": "Собака",
        "meaning": "Буря, очищение через страдание, трансформация",
    },
    {
        "sanskrit": "Punarvasu",
        "ru": "Пунарвасу",
        "ruler": "Юпитер",
        "deity": "Адити (Мать богов)",
        "symbol": "Колчан стрел",
        "guna": "Саттва",
        "animal": "Кошка",
        "meaning": "Возвращение, возрождение, обновление",
    },
    {
        "sanskrit": "Pushya",
        "ru": "Пушья",
        "ruler": "Сатурн",
        "deity": "Брихаспати (Гуру богов)",
        "symbol": "Вымя коровы",
        "guna": "Саттва",
        "animal": "Козёл",
        "meaning": "Питание, благословение, процветание",
    },
    {
        "sanskrit": "Ashlesha",
        "ru": "Ашлеша",
        "ruler": "Меркурий",
        "deity": "Наги (Змеиные божества)",
        "symbol": "Кольцо змеи",
        "guna": "Тамас",
        "animal": "Кошка",
        "meaning": "Обвитие, гипноз, скрытая сила",
    },
    {
        "sanskrit": "Magha",
        "ru": "Магха",
        "ruler": "Кету",
        "deity": "Питры (Предки)",
        "symbol": "Царский трон",
        "guna": "Тамас",
        "animal": "Крыса",
        "meaning": "Власть, наследие, честь предков",
    },
    {
        "sanskrit": "Purva Phalguni",
        "ru": "Пурва Пхалгуни",
        "ruler": "Венера",
        "deity": "Бхага (Бог удачи)",
        "symbol": "Ноги кровати",
        "guna": "Раджас",
        "animal": "Крыса",
        "meaning": "Наслаждение, любовь, творчество",
    },
    {
        "sanskrit": "Uttara Phalguni",
        "ru": "Уттара Пхалгуни",
        "ruler": "Солнце",
        "deity": "Арьяман (Бог дружбы)",
        "symbol": "Ноги кровати",
        "guna": "Саттва",
        "animal": "Корова",
        "meaning": "Дружба, союз, завершённость",
    },
    {
        "sanskrit": "Hasta",
        "ru": "Хаста",
        "ruler": "Луна",
        "deity": "Савитар (Солнечное божество)",
        "symbol": "Рука / Ладонь",
        "guna": "Саттва",
        "animal": "Буйвол",
        "meaning": "Мастерство, ремесло, целительство руками",
    },
    {
        "sanskrit": "Chitra",
        "ru": "Читра",
        "ruler": "Марс",
        "deity": "Тваштар (Божественный зодчий)",
        "symbol": "Жемчужина / Яркий",
        "guna": "Тамас",
        "animal": "Тигр",
        "meaning": "Творчество, красота, архитектура",
    },
    {
        "sanskrit": "Swati",
        "ru": "Свати",
        "ruler": "Раху",
        "deity": "Ваю (Бог ветра)",
        "symbol": "Коралл / Молодой побег",
        "guna": "Тамас",
        "animal": "Буйвол",
        "meaning": "Независимость, ветер, свобода",
    },
    {
        "sanskrit": "Vishakha",
        "ru": "Вишакха",
        "ruler": "Юпитер",
        "deity": "Индра-Агни",
        "symbol": "Триумфальная арка",
        "guna": "Раджас",
        "animal": "Тигр",
        "meaning": "Целеустремлённость, достижение, двойственность",
    },
    {
        "sanskrit": "Anuradha",
        "ru": "Анурадха",
        "ruler": "Сатурн",
        "deity": "Митра (Бог дружбы)",
        "symbol": "Цветок лотоса",
        "guna": "Саттва",
        "animal": "Олень",
        "meaning": "Дружба, преданность, успех через связь",
    },
    {
        "sanskrit": "Jyeshtha",
        "ru": "Джьештха",
        "ruler": "Меркурий",
        "deity": "Индра (Царь богов)",
        "symbol": "Серьга / Талисман",
        "guna": "Тамас",
        "animal": "Олень",
        "meaning": "Старшинство, власть, защита",
    },
    {
        "sanskrit": "Mula",
        "ru": "Мула",
        "ruler": "Кету",
        "deity": "Ниррити (Богиня разрушения)",
        "symbol": "Пучок корней",
        "guna": "Тамас",
        "animal": "Собака",
        "meaning": "Корень, истина, разрушение иллюзий",
    },
    {
        "sanskrit": "Purva Ashadha",
        "ru": "Пурва Ашадха",
        "ruler": "Венера",
        "deity": "Апах (Водное божество)",
        "symbol": "Веер / Трость",
        "guna": "Раджас",
        "animal": "Обезьяна",
        "meaning": "Непобедимость, очищение, амбиция",
    },
    {
        "sanskrit": "Uttara Ashadha",
        "ru": "Уттара Ашадха",
        "ruler": "Солнце",
        "deity": "Вишвадевы (Десять божеств)",
        "symbol": "Бивень слона",
        "guna": "Саттва",
        "animal": "Коровь",
        "meaning": "Победа, лидерство, окончательный триумф",
    },
    {
        "sanskrit": "Shravana",
        "ru": "Шравана",
        "ruler": "Луна",
        "deity": "Вишну (Хранитель)",
        "symbol": "Ухо / Три следа",
        "guna": "Саттва",
        "animal": "Обезьяна",
        "meaning": "Слушание, обучение, мудрость",
    },
    {
        "sanskrit": "Dhanishta",
        "ru": "Дхаништха",
        "ruler": "Марс",
        "deity": "Васу (Восемь божеств богатства)",
        "symbol": "Барабан / Флейта",
        "guna": "Раджас",
        "animal": "Лев",
        "meaning": "Богатство, музыка, ритм",
    },
    {
        "sanskrit": "Shatabhisha",
        "ru": "Шатабхиша",
        "ruler": "Раху",
        "deity": "Варуна (Бог космического порядка)",
        "symbol": "Пустой круг / 100 лекарств",
        "guna": "Тамас",
        "animal": "Лошадь",
        "meaning": "Исцеление, тайна, скрытое знание",
    },
    {
        "sanskrit": "Purva Bhadrapada",
        "ru": "Пурва Бхадрапада",
        "ruler": "Юпитер",
        "deity": "Аджайкапал (Огненный череп)",
        "symbol": "Меч / Два лица",
        "guna": "Раджас",
        "animal": "Лев",
        "meaning": "Двойственность, аскеза, духовный огонь",
    },
    {
        "sanskrit": "Uttara Bhadrapada",
        "ru": "Уттара Бхадрапада",
        "ruler": "Сатурн",
        "deity": "Ахир Будхнья (Змей глубин)",
        "symbol": "Двойной клин / Ноги кровати",
        "guna": "Саттва",
        "animal": "Корова",
        "meaning": "Глубина, мудрость, завершение",
    },
    {
        "sanskrit": "Revati",
        "ru": "Ревати",
        "ruler": "Меркурий",
        "deity": "Пушан (Бог путешествий)",
        "symbol": "Барабан / Рыба",
        "guna": "Саттва",
        "animal": "Слон",
        "meaning": "Процветание, путь, завершение цикла",
    },
]

assert len(NAKSHATRA_DATA) == 27, f"Expected 27 nakshatras, got {len(NAKSHATRA_DATA)}"

# Маппинг управитель накшатры → планета (для Вимшоттари Даша)
NAKSHATRA_RULER_DASHA: Dict[str, str] = {n["ru"]: n["ruler"] for n in NAKSHATRA_DATA}


def calculate_nakshatra(moon_longitude: float) -> Dict[str, Any]:
    """Рассчитать Накшатру Лицы по сидерической долготе Луны.

    Args:
        moon_longitude: Сидерическая долгота Луны [0, 360)

    Returns:
        Словарь с данными накшатры:
        - index (0-26), sanskrit, ru, pada (1-4),
        - ruler, deity, symbol, guna, animal, meaning,
        - start_deg, end_deg, longitude_in_nakshatra
    """
    lon = _deg_norm(moon_longitude)
    nak_idx = int(lon / NAKSHATRA_SIZE)
    if nak_idx >= 27:
        nak_idx = 26

    nak = NAKSHATRA_DATA[nak_idx]
    start_deg = nak_idx * NAKSHATRA_SIZE
    end_deg = (nak_idx + 1) * NAKSHATRA_SIZE
    lon_in_nak = lon - start_deg

    # Пада: каждая накшатра делится на 4 пады по 3°20'
    pada_size = NAKSHATRA_SIZE / 4.0  # ≈ 3.3333°
    pada = int(lon_in_nak / pada_size) + 1
    if pada > 4:
        pada = 4

    return {
        "index": nak_idx,
        "sanskrit": nak["sanskrit"],
        "ru": nak["ru"],
        "pada": pada,
        "ruler": nak["ruler"],
        "deity": nak["deity"],
        "symbol": nak["symbol"],
        "guna": nak["guna"],
        "animal": nak["animal"],
        "meaning": nak["meaning"],
        "start_deg": round(start_deg, 4),
        "end_deg": round(end_deg, 4),
        "longitude_in_nakshatra": round(lon_in_nak, 4),
    }


# ════════════════════════════════════════════════════════════════
#  3. ВИМШОТТАРИ ДАША
# ════════════════════════════════════════════════════════════════

# Периоды Махадаша в годах: (планета_ключ, длительность_лет, название_ру)
DASHA_PERIODS: List[Tuple[str, int, str]] = [
    ("ketu",       7, "Кету"),
    ("venus",     20, "Венера (Шукра)"),
    ("sun",        6, "Солнце (Сурья)"),
    ("moon",      10, "Луна (Чандра)"),
    ("mars",       7, "Марс (Мангал)"),
    ("rahu",      18, "Раху"),
    ("jupiter",   16, "Юпитер (Гуру)"),
    ("saturn",    19, "Сатурн (Шани)"),
    ("mercury",   17, "Меркурий (Буддха)"),
]

TOTAL_DASHA_YEARS = sum(p[1] for p in DASHA_PERIODS)  # 120 лет

# Маппинг: русское имя управителя → ключ планеты в DASHA_PERIODS
RULER_TO_DASHA_KEY: Dict[str, str] = {
    "Кету": "ketu",
    "Венера": "venus",
    "Солнце": "sun",
    "Луна": "moon",
    "Марс": "mars",
    "Раху": "rahu",
    "Юпитер": "jupiter",
    "Сатурн": "saturn",
    "Меркурий": "mercury",
}


def calculate_vimshottari_dasha(
    birth_jd: float,
    moon_longitude: float,
    current_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Рассчитать Вимшоттари Дашу с реальными датами.

    Args:
        birth_jd: Юлианский день рождения
        moon_longitude: Сидерическая долгота Луны
        current_date: Текущая дата (для определения текущей Даши).
                      По умолчанию — сейчас.

    Returns:
        Словарь:
        - mahadashas: список всех Махадаша периодов
        - current_mahadasha: текущий Махадаша
        - current_antardasha: текущий Антардаша
        - birth_nakshatra_ruler: управитель накшатры рождения
    """
    if current_date is None:
        current_date = datetime.now(timezone.utc)

    nak = calculate_nakshatra(moon_longitude)
    ruler_ru = nak["ruler"]
    dasha_key = RULER_TO_DASHA_KEY.get(ruler_ru)

    if dasha_key is None:
        logger.warning("Неизвестный управитель накшатры: %s", ruler_ru)
        dasha_key = "ketu"

    # Найти индекс стартовой Даши (управитель накшатры рождения)
    start_idx = next(i for i, p in enumerate(DASHA_PERIODS) if p[0] == dasha_key)

    # Рассчитать сколько процентов накшатры уже пройдено
    # Это определяет какая часть стартовой Даши уже прошла к моменту рождения
    lon_in_nak = nak["longitude_in_nakshatra"]
    fraction_passed = lon_in_nak / NAKSHATRA_SIZE  # 0..1

    # Оставшаяся часть стартовой Даши
    start_period_years = DASHA_PERIODS[start_idx][1]
    remaining_years = start_period_years * (1.0 - fraction_passed)

    # Строим все Махадаша периоды
    mahadashas: List[Dict[str, Any]] = []
    birth_dt = _jd_to_datetime(birth_jd)
    period_start = birth_dt
    total_years = 0.0

    for offset in range(9):
        idx = (start_idx + offset) % 9
        key, years, name = DASHA_PERIODS[idx]

        if offset == 0:
            # Первый период — неполный
            period_years = remaining_years
        else:
            period_years = float(years)

        period_end_dt = period_start + timedelta(days=period_years * 365.25)

        mahadashas.append({
            "planet_key": key,
            "planet_name": name,
            "duration_years": round(period_years, 3),
            "start_date": period_start.strftime("%d.%m.%Y"),
            "end_date": period_end_dt.strftime("%d.%m.%Y"),
            "start_jd": _datetime_to_jd(period_start) if hasattr(_datetime_to_jd, '__call__') else 0,
            "is_partial": offset == 0,
        })

        total_years += period_years
        period_start = period_end_dt

        # Прерываем после 120 лет (полный цикл)
        if total_years >= TOTAL_DASHA_YEARS:
            break

    # Найти текущий Махадаша
    current_mahadasha = None
    current_antardasha = None

    for md in mahadashas:
        start_jd_val = md.get("start_jd", 0)
        # Вычислим end_jd
        end_jd_val = start_jd_val + md["duration_years"] * 365.25
        current_jd = _datetime_to_jd(current_date) if current_date else _datetime_to_jd(
            datetime.now(timezone.utc)
        )

        if start_jd_val <= current_jd < end_jd_val:
            current_mahadasha = md

            # Рассчитать Антардаша внутри текущего Махадаша
            current_antardasha = _calculate_antardashas(
                md, birth_jd, current_jd
            )
            break

    return {
        "birth_nakshatra_ruler": ruler_ru,
        "mahadashas": mahadashas,
        "current_mahadasha": current_mahadasha,
        "current_antardasha": current_antardasha,
    }


def _calculate_antardashas(
    mahadasha: Dict[str, Any],
    birth_jd: float,
    current_jd: float,
) -> Optional[Dict[str, Any]]:
    """Рассчитать Антардаша (периоды внутри Махадаша).

    Антардаша следуют в том же порядке, начиная с управителя Махадаша.
    Длительность пропорциональна длительности Махадаша.
    """
    md_key = mahadasha["planet_key"]
    md_years = mahadasha["duration_years"]

    # Найти индекс начала
    start_idx = next(
        (i for i, p in enumerate(DASHA_PERIODS) if p[0] == md_key), 0
    )

    # Антардаша: пропорциональные периоды
    # Доля каждого антардаша = (период_планеты / 120) * период_махадаша
    md_start_jd = mahadasha.get("start_jd", 0)
    if md_start_jd == 0:
        return None

    antar_start_jd = md_start_jd
    for offset in range(9):
        idx = (start_idx + offset) % 9
        key, years, name = DASHA_PERIODS[idx]
        antar_years = (years / TOTAL_DASHA_YEARS) * md_years
        antar_end_jd = antar_start_jd + antar_years * 365.25

        if antar_start_jd <= current_jd < antar_end_jd:
            # Нашли текущий Антардаша
            start_dt = _jd_to_datetime(antar_start_jd)
            end_dt = _jd_to_datetime(antar_end_jd)
            return {
                "planet_key": key,
                "planet_name": name,
                "duration_years": round(antar_years, 3),
                "start_date": start_dt.strftime("%d.%m.%Y"),
                "end_date": end_dt.strftime("%d.%m.%Y"),
            }

        antar_start_jd = antar_end_jd

    return None


def _jd_to_datetime(jd: float) -> datetime:
    """Конвертировать Юлианский день в datetime (UTC).

    Упрощённый алгоритм обратного преобразования.
    """
    # Алгоритм Мёйса (обратный)
    jd_val = jd + 0.5
    Z = int(jd_val)
    F = jd_val - Z

    if Z < 2299161:
        A = Z
    else:
        alpha = int((Z - 1867216.25) / 36524.25)
        A = Z + 1 + alpha - int(alpha / 4)

    B = A + 1524
    C = int((B - 122.1) / 365.25)
    D = int(365.25 * C)
    E = int((B - D) / 30.6001)

    day = B - D - int(30.6001 * E) + F
    month = E - 1 if E < 14 else E - 13
    year = C - 4716 if month > 2 else C - 4715

    # Извлечь часы/минуты из дробной части дня
    day_int = int(day)
    frac = day - day_int
    hours = int(frac * 24)
    minutes = int((frac * 24 - hours) * 60)
    seconds = int(((frac * 24 - hours) * 60 - minutes) * 60)

    try:
        return datetime(year, month, day_int, hours, minutes, seconds)
    except ValueError:
        # Fallback для краевых случаев
        return datetime(year, month, day_int, 12, 0, 0)


# ════════════════════════════════════════════════════════════════
#  4. АТМА-КАРАКА
# ════════════════════════════════════════════════════════════════

# Интерпретация Атма-караки
ATMAKARAKA_INTERPRETATIONS: Dict[str, str] = {
    "sun": "Ваша душа связана с лидерством, властью, самоутверждением. Главная задача — развивать уверенность и не подавлять других. Отец и его родовая линия играют ключевую роль.",
    "moon": "Ваша душа связана с эмоциями, заботой, интуицией. Главная задача — развивать эмоциональную устойчивость и не растворяться в чужих чувствах. Мать и её родовая линия — ключ.",
    "mars": "Ваша душа связана с действием, смелостью, борьбой. Главная задача — использовать силу конструктивно, а не разрушительно. Конфликты — ваши учителя.",
    "mercury": "Ваша душа связана с интеллектом, коммуникацией, бизнесом. Главная задача — использовать ум на благо, а не для манипуляций. Обучение — путь души.",
    "jupiter": "Ваша душа связана с мудростью, учительством, благородством. Главная задача — быть истинным учителем, а не догматиком. Дети и ученики — ваше наследие.",
    "venus": "Ваша душа связана с любовью, красотой, гармонией. Главная задача — любить безусловно, не привязываясь к форме. Отношения — ваш духовный путь.",
    "saturn": "Ваша душа связана с кармой, дисциплиной, терпением. Главная задача — нести ответственность без горечи. Самый сложный, но самый глубокий путь.",
    "north_node": "Ваша душа связана с амбициями, иллюзиями, новым опытом. Главная задача — трансформировать одержимость в преданность. Чужие культуры — ваш путь.",
}


def calculate_atmakaraka(
    planet_positions: Dict[str, float],
    ayanamsa: float,
    include_rahu: bool = False,
) -> Dict[str, Any]:
    """Определить Атма-караку — планету с наивысшим градусом в знаке.

    В Джйотиш рассматриваются 7 (или 8 с Раху) планет.
    Атма-карака = планета с максимальным градусом (не более 30°) в знаке.

    Args:
        planet_positions: Тропические долготы планет из calc_all_planets()
        ayanamsa: Лахири Аянамша в градусах
        include_rahu: Включать ли Раху (классически — нет)

    Returns:
        Словарь: planet, planet_name_ru, degree_in_sign, sidereal_longitude,
                  sign, nakshatra, interpretation
    """
    # Планеты для расчёта Атма-караки
    ak_planets = ['sun', 'moon', 'mars', 'mercury', 'jupiter', 'venus', 'saturn']
    if include_rahu:
        ak_planets.append('north_node')

    best_planet = None
    best_degree_in_sign = -1.0
    best_sidereal_lon = 0.0

    for planet in ak_planets:
        tropical_lon = planet_positions.get(planet, 0.0)
        sidereal_lon = (tropical_lon - ayanamsa) % 360.0
        degree_in_sign = sidereal_lon % 30.0

        if degree_in_sign > best_degree_in_sign:
            best_degree_in_sign = degree_in_sign
            best_planet = planet
            best_sidereal_lon = sidereal_lon

    if best_planet is None:
        best_planet = 'sun'
        best_sidereal_lon = 0.0
        best_degree_in_sign = 0.0

    # Накшатра Атма-караки
    ak_nak = calculate_nakshatra(best_sidereal_lon)
    sign_idx = int(best_sidereal_lon / 30.0)
    sign_name = SIGNS_RU[sign_idx] if sign_idx < 12 else "Неизвестно"
    planet_name_ru = JYOTISH_PLANET_NAMES_RU.get(best_planet, PLANET_NAMES_RU.get(best_planet, best_planet))
    interpretation = ATMAKARAKA_INTERPRETATIONS.get(best_planet, "Нет интерпретации.")

    return {
        "planet": best_planet,
        "planet_name_ru": planet_name_ru,
        "degree_in_sign": round(best_degree_in_sign, 4),
        "sidereal_longitude": round(best_sidereal_lon, 4),
        "sign": sign_name,
        "nakshatra": ak_nak["ru"],
        "nakshatra_pada": ak_nak["pada"],
        "interpretation": interpretation,
    }


# ════════════════════════════════════════════════════════════════
#  5. НАВАМША (D9)
# ════════════════════════════════════════════════════════════════

# Навамша: каждый знак делится на 9 навамш по 3°20' (3.3333°)
NAVAMSHA_SIZE = 30.0 / 9.0  # ≈ 3.3333°

# Стартовый знак навамш для каждого знака
# Для огненных знаков (Овен, Лев, Стрелец) навамши начинаются с Овна
# Для земных (Телец, Дева, Козерог) — с Козерога
# Для воздушных (Близнецы, Весы, Водолей) — с Весов
# Для водных (Рак, Скорпион, Рыбы) — с Рака
NAVAMSHA_START_SIGN = {
    0: 0,    # Овен → Овен
    1: 9,    # Телец → Козерог
    2: 6,    # Близнецы → Весы
    3: 3,    # Рак → Рак
    4: 0,    # Лев → Овен
    5: 9,    # Дева → Козерог
    6: 6,    # Весы → Весы
    7: 3,    # Скорпион → Рак
    8: 0,    # Стрелец → Овен
    9: 9,    # Козерог → Козерог
    10: 6,   # Водолей → Весы
    11: 3,   # Рыбы → Рак
}


def _calculate_navamsha_sign(sidereal_lon: float) -> Tuple[int, int]:
    """Определить знак и навамшу для сидерической долготы.

    Returns:
        (navamsha_sign_index, navamsha_number_in_sign)
        navamsha_number_in_sign: 1-9
    """
    lon = _deg_norm(sidereal_lon)
    sign_idx = int(lon / 30.0)
    if sign_idx >= 12:
        sign_idx = 11

    deg_in_sign = lon - sign_idx * 30.0
    nav_idx = int(deg_in_sign / NAVAMSHA_SIZE)
    if nav_idx >= 9:
        nav_idx = 8

    # Навамша-знак
    start_sign = NAVAMSHA_START_SIGN.get(sign_idx, 0)
    nav_sign = (start_sign + nav_idx) % 12

    return nav_sign, nav_idx + 1


def calculate_navamsha(
    planet_positions: Dict[str, float],
    ayanamsa: float,
) -> Dict[str, Any]:
    """Рассчитать Навамша (D9) карту.

    Args:
        planet_positions: Тропические долготы планет из calc_all_planets()
        ayanamsa: Лахири Аянамша

    Returns:
        Словарь:
        - planets: {planet_key: {sign, sign_ru, navamsha_num, navamsha_lon}}
        - ayanamsa: использованная аянамша
    """
    nav_planets: Dict[str, Dict[str, Any]] = {}

    for planet in JYOTISH_PLANETS:
        tropical_lon = planet_positions.get(planet, 0.0)
        sidereal_lon = (tropical_lon - ayanamsa) % 360.0

        nav_sign_idx, nav_num = _calculate_navamsha_sign(sidereal_lon)

        # Долгота внутри навамша-знака
        # Позиция внутри исходного знака → навамша → позиция внутри навамша-знака
        sign_idx = int(sidereal_lon / 30.0)
        deg_in_sign = sidereal_lon - sign_idx * 30.0
        deg_in_navamsha = deg_in_sign % NAVAMSHA_SIZE

        # Навамша-долгота = навамша-знак * 30 + позиция внутри навамши
        nav_lon = nav_sign_idx * 30.0 + deg_in_navamsha

        nav_planets[planet] = {
            "sign_index": nav_sign_idx,
            "sign": SIGNS_RU[nav_sign_idx],
            "navamsha_number": nav_num,
            "navamsha_longitude": round(nav_lon, 4),
        }

    # Кету всегда оппозит Раху
    rahu_nav = nav_planets.get('north_node', {})
    if rahu_nav:
        ketu_sign = (rahu_nav.get("sign_index", 0) + 6) % 12
        nav_planets['south_node'] = {
            "sign_index": ketu_sign,
            "sign": SIGNS_RU[ketu_sign],
            "navamsha_number": rahu_nav.get("navamsha_number", 1),
            "navamsha_longitude": round((rahu_nav.get("navamsha_longitude", 0) + 180.0) % 360.0, 4),
        }

    return {
        "planets": nav_planets,
        "ayanamsa": round(ayanamsa, 6),
    }


# ════════════════════════════════════════════════════════════════
#  6. ПАНЧАНГ
# ════════════════════════════════════════════════════════════════

# Дни недели
VARA_NAMES_RU = [
    "Воскресенье (Равивара — Солнце)",
    "Понедельник (Сомавара — Луна)",
    "Вторник (Мангалвара — Марс)",
    "Среда (Будхавара — Меркурий)",
    "Четверг (Гурувара — Юпитер)",
    "Пятница (Шукравара — Венера)",
    "Суббота (Шанивара — Сатурн)",
]

# 27 Йог (лунно-солнечные комбинации)
YOGA_NAMES: List[str] = [
    "Вишкумбха", "Прити", "Аюшман", "Саубхагья", "Шобхана",
    "Атиганда", "Сукарман", "Дхрити", "Шула", "Ганда",
    "Вриддхи", "Дхрува", "Вьягхата", "Харшана", "Ваджра",
    "Сиддхи", "Вьятипата", "Вариян", "Паридхи", "Шива",
    "Сиддха", "Садхья", "Шубха", "Шукла", "Брахма",
    "Индра", "Вайдхрити",
]

# 11 Караны (типов)
KARANA_NAMES: List[str] = [
    "Бава", "Балава", "Каулава", "Таитула", "Гари",
    "Ваниджа", "Вишти (Бхадра)", "Шакуни", "Чатушпада",
    "Нага", "Кимстугхна",
]


def calculate_panchanga(
    birth_jd: float,
    moon_sidereal_lon: float,
    sun_sidereal_lon: float,
) -> Dict[str, Any]:
    """Рассчитать Панчанг — 5 элементов ведического дня рождения.

    Args:
        birth_jd: Юлианский день рождения
        moon_sidereal_lon: Сидерическая долгота Луны
        sun_sidereal_lon: Сидерическая долгота Солнца

    Returns:
        Словарь с 5 элементами: vara, tithi, nakshatra, yoga, karana
    """
    # 1. ВАРА — день недели
    birth_dt = _jd_to_datetime(birth_jd)
    # Python: Monday=0, Sunday=6 → Ведический: Sunday=0, Saturday=6
    python_weekday = birth_dt.weekday()
    vara_idx = (python_weekday + 1) % 7  # Конвертация: Monday=1, Sunday=0

    # 2. ТИТХИ — лунная фаза
    # Титхи = (Луна - Солнце) / 12°
    tithi_diff = (moon_sidereal_lon - sun_sidereal_lon) % 360.0
    tithi_number = int(tithi_diff / 12.0) + 1  # 1-30
    if tithi_number > 30:
        tithi_number = 30

    # Определяем пакшу и название
    if tithi_number <= 15:
        paksha = "Шукла-пакша (растущая Луна)"
        tithi_in_paksha = tithi_number
    else:
        paksha = "Кришна-пакша (убывающая Луна)"
        tithi_in_paksha = tithi_number - 15

    # Названия титхи
    tithi_names = [
        "Пратипат", "Двития", "Трития", "Чатуртхи", "Панчами",
        "Шаштхи", "Саптами", "Аштами", "Навами", "Дашами",
        "Экадаши", "Двадаши", "Трайодаши", "Чатурдаши",
    ]
    if tithi_number == 15:
        tithi_name = "Пурнима (Полнолуние)"
    elif tithi_number == 30:
        tithi_name = "Амавасья (Новолуние)"
    elif tithi_in_paksha <= 14:
        tithi_name = tithi_names[tithi_in_paksha - 1]
    else:
        tithi_name = f"Титхи {tithi_number}"

    # 3. НАКШАТРА — рассчитывается отдельно
    nakshatra = calculate_nakshatra(moon_sidereal_lon)

    # 4. ЙОГА — лунно-солнечная комбинация
    # Йога = (Луна + Солнце) / 13°20'
    yoga_lon = (moon_sidereal_lon + sun_sidereal_lon) % 360.0
    yoga_idx = int(yoga_lon / NAKSHATRA_SIZE)  # 0-26
    if yoga_idx >= 27:
        yoga_idx = 26

    # 5. КАРАНА — половина титхи
    # Карана = титхи * 2 (60 вариантов, но 11 уникальных типов)
    karna_within_tithi = (tithi_diff % 12.0) < 6.0  # первая или вторая половина
    # Упрощённо: 7 повторяющихся каран + 4 фиксированных
    if tithi_number == 1 and not karna_within_tithi:
        karana_name = "Кимстугхна"
    elif tithi_number == 30 and karna_within_tithi:
        karana_name = "Шакуни"
    elif tithi_number == 29 and not karna_within_tithi:
        karana_name = "Чатушпада"
    elif tithi_number == 29 and karna_within_tithi:
        karana_name = "Нага"
    else:
        # 7 повторяющихся каран
        karana_cycle_idx = ((tithi_number - 1) * 2 + (0 if karna_within_tithi else 1)) % 7
        karana_name = KARANA_NAMES[karana_cycle_idx]

    return {
        "vara": {
            "index": vara_idx,
            "name": VARA_NAMES_RU[vara_idx],
            "day_of_week": birth_dt.strftime("%A"),
        },
        "tithi": {
            "number": tithi_number,
            "name": tithi_name,
            "paksha": paksha,
            "tithi_in_paksha": tithi_in_paksha,
        },
        "nakshatra": nakshatra,
        "yoga": {
            "index": yoga_idx,
            "name": YOGA_NAMES[yoga_idx],
        },
        "karana": {
            "name": karana_name,
        },
    }


# ════════════════════════════════════════════════════════════════
#  7. МАСТЕР-ФУНКЦИЯ
# ════════════════════════════════════════════════════════════════

def calculate_all_jyotish(
    day: int,
    month: int,
    year: int,
    birth_time: str = "",
    birth_place: str = "",
) -> Dict[str, Any]:
    """Рассчитать ВСЕ данные Джйотиш для даты рождения.

    Args:
        day, month, year: Дата рождения
        birth_time: Время рождения ('HH:MM' или 'HH:MM Город')
        birth_place: Место рождения (для часового пояса)

    Returns:
        Полный словарь со всеми расчётами Джйотиш
    """
    # ── Парсинг времени и пояс ──
    if not birth_time:
        hours, minutes = 12, 0
    else:
        hours, minutes = _parse_birth_time(birth_time)

    tz_offset = _get_timezone_offset(birth_place) if birth_place else 3.0

    # ── Юлианский день ──
    # UT = местное время - смещение пояса
    ut_hours = hours - tz_offset
    birth_dt = datetime(year, month, day, int(ut_hours), int((ut_hours % 1) * 60), tzinfo=timezone.utc)
    birth_jd = _datetime_to_jd(birth_dt)

    # ── Планетные позиции (тропические) ──
    tropical_positions = calc_all_planets(birth_jd)

    # ── Аянамша ──
    ayanamsa = calculate_lahiri_ayanamsa_jd(birth_jd)

    # ── Сидерические позиции ──
    sidereal_positions: Dict[str, float] = {}
    for planet, lon in tropical_positions.items():
        sidereal_positions[planet] = (lon - ayanamsa) % 360.0

    # ── Накшатра Луны ──
    moon_sidereal = sidereal_positions.get('moon', 0.0)
    moon_nakshatra = calculate_nakshatra(moon_sidereal)

    # ── Вимшоттари Даша ──
    dasha = calculate_vimshottari_dasha(birth_jd, moon_sidereal)

    # ── Атма-карака ──
    atmakaraka = calculate_atmakaraka(tropical_positions, ayanamsa, include_rahu=False)

    # ── Навамша ──
    navamsha = calculate_navamsha(tropical_positions, ayanamsa)

    # ── Панчанг ──
    sun_sidereal = sidereal_positions.get('sun', 0.0)
    panchanga = calculate_panchanga(birth_jd, moon_sidereal, sun_sidereal)

    # ── Раши (сидерические знаки) для всех планет ──
    planet_rashis: Dict[str, Dict[str, Any]] = {}
    for planet in JYOTISH_PLANETS:
        sid_lon = sidereal_positions.get(planet, 0.0)
        sign_idx, deg, min_frac, sign_name = longitude_to_sign_degree(sid_lon)
        planet_rashis[planet] = {
            "sign_index": sign_idx,
            "sign": sign_name,
            "degree_in_sign": round(deg + min_frac / 60.0, 4),
            "sidereal_longitude": round(sid_lon, 4),
            "display": longitude_to_dms(sid_lon),
        }

    # Кету — оппозит Раху
    rahu_sid = sidereal_positions.get('north_node', 0.0)
    ketu_sid = (rahu_sid + 180.0) % 360.0
    sign_idx_k, deg_k, min_frac_k, sign_name_k = longitude_to_sign_degree(ketu_sid)
    planet_rashis['south_node'] = {
        "sign_index": sign_idx_k,
        "sign": sign_name_k,
        "degree_in_sign": round(deg_k + min_frac_k / 60.0, 4),
        "sidereal_longitude": round(ketu_sid, 4),
        "display": longitude_to_dms(ketu_sid),
    }

    # ── Джанма-Раши (знак Луны) ──
    moon_sign_idx = int(moon_sidereal / 30.0)
    janma_rashi = SIGNS_RU[moon_sign_idx] if moon_sign_idx < 12 else "Неизвестно"

    # ── Лагна (Асцендент) — приближённо по Солнцу + поправка ──
    # Точный асцендент требует широты места, используем приближение
    # Для простой оценки: лагна ≈ сидерическое Солнце + (UT_hours * 15°) - 90°
    # (очень грубое приближение, лучше чем ничего)
    approx_lagna = (sun_sidereal + (ut_hours % 24) * 15.0 - 90.0) % 360.0
    lagna_sign_idx = int(approx_lagna / 30.0)
    lagna_sign = SIGNS_RU[lagna_sign_idx] if lagna_sign_idx < 12 else "Неизвестно"

    return {
        "birth_info": {
            "date": f"{day:02d}.{month:02d}.{year}",
            "time": f"{hours:02d}:{minutes:02d}",
            "place": birth_place or "не указано",
            "tz_offset": tz_offset,
            "jd": round(birth_jd, 4),
        },
        "ayanamsa": round(ayanamsa, 6),
        "sidereal_positions": {k: round(v, 4) for k, v in sidereal_positions.items()},
        "planet_rashis": planet_rashis,
        "janma_rashi": janma_rashi,
        "lagna_approx": {
            "sign": lagna_sign,
            "longitude": round(approx_lagna, 4),
            "note": "Приближённая Лагна (требуется широта для точного расчёта)",
        },
        "moon_nakshatra": moon_nakshatra,
        "dasha": dasha,
        "atmakaraka": atmakaraka,
        "navamsha": navamsha,
        "panchanga": panchanga,
    }


# ════════════════════════════════════════════════════════════════
#  8. ФОРМАТИРОВАНИЕ КОНТЕКСТА ДЛЯ ИИ
# ════════════════════════════════════════════════════════════════

def build_jyotish_calc_context(
    day: int,
    month: int,
    year: int,
    birth_time: str = "",
    birth_place: str = "",
) -> str:
    """Построить подробный текстовый контекст расчёта Джйотиш для ИИ.

    Это НЕ галлюцинация — все данные основаны на реальных астрономических расчётах.
    ИИ должен ИНТЕРПРЕТИРОВАТЬ эти данные, а не придумывать.
    """
    try:
        data = calculate_all_jyotish(day, month, year, birth_time, birth_place)
    except Exception as e:
        logger.error("Ошибка расчёта Джйотиш: %s", e, exc_info=True)
        return f"⚠️ ОШИБКА РАСЧЁТА ДЖЙОТИШ: {e}\nИспользуйте справочные данные."

    lines: List[str] = []

    lines.append("═" * 60)
    lines.append("  РАСЧЁТ ДЖЙОТИШ (ВЕДИЧЕСКАЯ АСТРОЛОГИЯ)")
    lines.append("  (Реальные астрономические расчёты — Лахири Аянамша)")
    lines.append("═" * 60)

    # ── Данные рождения ──
    bi = data["birth_info"]
    lines.append(f"Дата рождения: {bi['date']}")
    lines.append(f"Время рождения: {bi['time']} UTC+{bi['tz_offset']:.0f}")
    lines.append(f"Место рождения: {bi['place']}")
    lines.append(f"Юлианский день: {bi['jd']:.4f}")
    lines.append(f"Аянамша (Лахири): {data['ayanamsa']:.4f}°")

    # ── Джанма-Раши и Лагна ──
    lines.append("")
    lines.append("── ДЖАНМА-РАШИ (ЗНАК ЛУНЫ) ──")
    lines.append(f"  {data['janma_rashi']}")

    lines.append("")
    lines.append("── ЛАГНА (АСПЕНДЕНТ) ──")
    lagna = data["lagna_approx"]
    lines.append(f"  {lagna['sign']} (долгота: {lagna['longitude']:.2f}°)")
    lines.append(f"  ⚠️ {lagna['note']}")

    # ── Накшатра Луны ──
    lines.append("")
    lines.append("── НАКШАТРА ЛУНЫ ──")
    nak = data["moon_nakshatra"]
    lines.append(f"  {nak['sanskrit']} / {nak['ru']}")
    lines.append(f"  Пада: {nak['pada']}")
    lines.append(f"  Управитель: {nak['ruler']}")
    lines.append(f"  Божество: {nak['deity']}")
    lines.append(f"  Символ: {nak['symbol']}")
    lines.append(f"  Гуна: {nak['guna']}")
    lines.append(f"  Животное: {nak['animal']}")
    lines.append(f"  Значение: {nak['meaning']}")

    # ── Сидерические позиции планет ──
    lines.append("")
    lines.append("── СИДЕРИЧЕСКИЕ ПОЗИЦИИ ПЛАНЕТ ──")
    for planet in JYOTISH_PLANETS:
        pr = data["planet_rashis"].get(planet, {})
        name = JYOTISH_PLANET_NAMES_RU.get(planet, planet)
        sign = pr.get("sign", "?")
        deg = pr.get("degree_in_sign", 0)
        display = pr.get("display", "?")
        lines.append(f"  {name:25s}  {display}  ({sign}, {deg:.2f}°)")

    # Кету
    pr_k = data["planet_rashis"].get("south_node", {})
    lines.append(f"  {'Кету':25s}  {pr_k.get('display', '?')}  ({pr_k.get('sign', '?')}, {pr_k.get('degree_in_sign', 0):.2f}°)")

    # ── Атма-карака ──
    lines.append("")
    lines.append("── АТМА-КАРАКА (ПЛАНЕТА ДУШИ) ──")
    ak = data["atmakaraka"]
    lines.append(f"  {ak['planet_name_ru']}")
    lines.append(f"  Градус в знаке: {ak['degree_in_sign']:.2f}° ({ak['sign']})")
    lines.append(f"  Накшатра: {ak['nakshatra']} (пада {ak['nakshatra_pada']})")
    lines.append(f"  Интерпретация: {ak['interpretation']}")

    # ── Навамша (D9) ──
    lines.append("")
    lines.append("── НАВАМША (D9) — КАРТА ДУШИ ──")
    nav = data["navamsha"]
    for planet in JYOTISH_PLANETS + ['south_node']:
        np = nav["planets"].get(planet, {})
        name = JYOTISH_PLANET_NAMES_RU.get(planet, planet)
        lines.append(f"  {name:25s}  {np.get('sign', '?')} (навамша #{np.get('navamsha_number', '?')})")

    # ── Вимшоттари Даша ──
    lines.append("")
    lines.append("── ВИМШОТТАРИ ДАША ──")
    dasha = data["dasha"]
    lines.append(f"  Управитель накшатры рождения: {dasha['birth_nakshatra_ruler']}")

    # Текущий Махадаша
    curr_md = dasha.get("current_mahadasha")
    if curr_md:
        lines.append(f"  Текущий Махадаша: {curr_md['planet_name']} "
                      f"({curr_md['start_date']} — {curr_md['end_date']})")
    else:
        lines.append("  Текущий Махадаша: не определён (дата вне расчётного диапазона)")

    # Текущий Антардаша
    curr_ad = dasha.get("current_antardasha")
    if curr_ad:
        lines.append(f"  Текущий Антардаша: {curr_ad['planet_name']} "
                      f"({curr_ad['start_date']} — {curr_ad['end_date']})")

    # Все Махадаша периоды
    lines.append("")
    lines.append("  Все Махадаша периоды:")
    for md in dasha["mahadashas"]:
        marker = " ◀ ТЕКУЩИЙ" if (curr_md and md["planet_key"] == curr_md["planet_key"]
                                   and md["start_date"] == curr_md["start_date"]) else ""
        partial = " (неполный)" if md.get("is_partial") else ""
        lines.append(f"    {md['planet_name']:20s}  "
                      f"{md['start_date']} — {md['end_date']}  "
                      f"({md['duration_years']:.1f} лет{partial}){marker}")

    # ── Панчанг ──
    lines.append("")
    lines.append("── ПАНЧАНГ (5 ЭЛЕМЕНТОВ ДНЯ РОЖДЕНИЯ) ──")
    pan = data["panchanga"]

    lines.append(f"  Вара: {pan['vara']['name']}")
    lines.append(f"  Титхи: {pan['tithi']['name']} (#{pan['tithi']['number']})")
    lines.append(f"    Пакша: {pan['tithi']['paksha']}")
    lines.append(f"  Накшатра: {nak['sanskrit']} / {nak['ru']} (пада {nak['pada']})")
    lines.append(f"  Йога: {pan['yoga']['name']} (#{pan['yoga']['index'] + 1})")
    lines.append(f"  Карана: {pan['karana']['name']}")

    lines.append("")
    lines.append("═" * 60)
    lines.append("  ⚠️ ИНСТРУКЦИЯ ДЛЯ ИИ:")
    lines.append("  Все данные выше ВЫЧИСЛЕНЫ программно из реальных эфемерид.")
    lines.append("  Интерпретируй эти данные, основываясь на принципах Джйотиш.")
    lines.append("  НЕ придумывай позиции планет или даты даша — они уже рассчитаны!")
    lines.append("═" * 60)

    return "\n".join(lines)
