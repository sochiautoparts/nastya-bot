"""Human Design (Bodygraph) Calculation Engine v2.0
====================================================

Реальный астрономический движок расчёта Дизайна Человека.
Использует Swiss Ephemeris (pyswisseph) для точных планетных позиций.
При отсутствии pyswisseph — упрощённые орбитальные расчёты (НЕ галлюцинации).

Расчёты:
  - Позиции 13 планет (Личность + Дизайн)
  - Дизайн-дата: Солнце на 88° раньше позиции рождения
  - Маппинг на 64 ворот (5.625° на ворот)
  - Линии (1-6), Цвета (1-6), Тоны (1-6) внутри ворот
  - 32 канала, определённость 9 центров
  - Тип, Авторитет, Профиль, Переменные (Color/Tone), Инкарнационный крест
  - Пищеварение, Среда, Перспектива, Мотивация

Автор: nastya-bot engine
"""

import math
import logging
from typing import Dict, List, Optional, Tuple, Any, Set
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
#  Swiss Ephemeris — опциональная зависимость
# ────────────────────────────────────────────────────────────────
try:
    import swisseph as swe
    _SWE_AVAILABLE = True
except ImportError:
    _SWE_AVAILABLE = False
    logger.warning("pyswisseph не установлен — используются упрощённые орбитальные расчёты. "
                   "Установите: pip install pyswisseph")

# ════════════════════════════════════════════════════════════════
#  1. КОНСТАНТЫ — ПОСЛЕДОВАТЕЛЬНОСТЬ ВОРОТ
# ════════════════════════════════════════════════════════════════

GATE_SIZE = 5.625  # 360 / 64

# Последовательность 64 ворот Дизайна Человека по зодиаку,
# начиная с 0° Овна. Каждый сектор = 5.625°.
# Позиция i: долгота i*5.625 ... (i+1)*5.625
GATE_SEQUENCE = [
    41, 19, 13, 49, 30, 55, 37, 63,   # 0° – 45° (Овен)
    22, 36, 25, 17, 21, 51, 42,  3,   # 45° – 90° (Овен-Телец-Близнецы)
    27, 24,  2, 23,  8, 20, 16, 35,   # 90° – 135° (Близнецы-Рак)
    45, 12, 15, 52, 39, 53, 62, 56,   # 135° – 180° (Рак-Лев-Дева)
    31, 33,  7,  4, 29, 59, 40, 64,   # 180° – 225° (Весы-Скорпион)
    47,  6, 46, 18, 48, 57, 32, 28,   # 225° – 270° (Скорпион-Стрелец-Козерог)
    50, 44,  1, 43, 14, 34,  9,  5,   # 270° – 315° (Козерог-Водолей)
    26, 11, 10, 58, 38, 54, 61, 60,   # 315° – 360° (Водолей-Рыбы)
]
assert len(GATE_SEQUENCE) == 64, f"GATE_SEQUENCE must have 64 entries, got {len(GATE_SEQUENCE)}"
assert len(set(GATE_SEQUENCE)) == 64, "GATE_SEQUENCE has duplicate gates!"

# Обратный маппинг: номер ворот → индекс в последовательности (0-63)
GATE_INDEX: Dict[int, int] = {g: i for i, g in enumerate(GATE_SEQUENCE)}

# ════════════════════════════════════════════════════════════════
#  3. ВОРОТА → ЦЕНТРЫ
# ════════════════════════════════════════════════════════════════

# Канонический маппинг: каждые ворот принадлежат ровно одному центру
GATE_CENTER: Dict[int, str] = {}
_center_gates = {
    'head':         [64, 61, 63],
    'ajna':         [47, 24,  4, 17, 43, 11],
    'throat':       [62, 23, 16, 35, 45, 12, 31, 33,  8, 20, 56],
    'g_center':     [25, 46, 10, 15,  2,  1, 13,  7],
    'heart':        [21, 40, 26, 51],
    'solar_plexus': [36, 22,  6, 55, 37, 30, 49],
    'sacral':       [59, 27,  3, 42, 14, 29,  5, 34,  9],
    'spleen':       [18, 48, 57, 32, 28, 50, 44],
    'root':         [58, 38, 54, 53, 60, 52, 39, 19, 41],
}
for _c, _gs in _center_gates.items():
    for _g in _gs:
        GATE_CENTER[_g] = _c
assert len(GATE_CENTER) == 64, f"GATE_CENTER must have 64 entries, got {len(GATE_CENTER)}"

CENTER_NAMES_RU: Dict[str, str] = {
    'head':         'Головной центр',
    'ajna':         'Центр Аджна',
    'throat':       'Горловой центр',
    'g_center':     'G-центр (Центр Джи)',
    'heart':        'Центр Сердце/Воля',
    'solar_plexus': 'Центр Солнечного Сплетения',
    'sacral':       'Крестцовый центр',
    'spleen':       'Центр Селезёнки',
    'root':         'Корневой центр',
}

MOTOR_CENTERS: Set[str] = {'heart', 'solar_plexus', 'root'}
AWARENESS_CENTERS: Set[str] = {'ajna', 'spleen', 'solar_plexus'}

# ════════════════════════════════════════════════════════════════
#  4. КАНАЛЫ (32 канала)
# ════════════════════════════════════════════════════════════════

# Каждый канал: (gate1, gate2, center1, center2, имя_ру)
CHANNELS: List[Tuple[int, int, str, str, str]] = [
    ( 1,  8, 'g_center',     'throat',       'Канал Самовыражения'),
    ( 2, 14, 'g_center',     'sacral',       'Канал Биения / Пульса'),
    ( 3, 60, 'sacral',       'root',         'Канал Мутации'),
    ( 4, 63, 'ajna',         'head',         'Канал Логики'),
    ( 5, 15, 'sacral',       'g_center',     'Канал Ритма'),
    ( 6, 59, 'solar_plexus', 'sacral',       'Канал Близости / Спаривания'),
    ( 7, 31, 'g_center',     'throat',       'Канал Альфы / Харизмы'),
    ( 9, 52, 'sacral',       'root',         'Канал Концентрации'),
    (10, 20, 'g_center',     'throat',       'Канал Пробуждения / Обязательства'),
    (10, 57, 'g_center',     'spleen',       'Канал Совершенной Формы / Выживания'),
    (11, 56, 'ajna',         'throat',       'Канал Любопытства'),
    (12, 22, 'throat',       'solar_plexus', 'Канал Открытости'),
    (13, 33, 'g_center',     'throat',       'Канал Блудного Сына / Слушания'),
    (16, 48, 'throat',       'spleen',       'Канал Таланта'),
    (17, 62, 'ajna',         'throat',       'Канал Принятия'),
    (18, 58, 'spleen',       'root',         'Канал Суждения / Радости Жизни'),
    (20, 34, 'throat',       'sacral',       'Канал Харизмы / Присутствия'),
    (21, 45, 'heart',        'throat',       'Канал Денег / Материи'),
    (23, 43, 'throat',       'ajna',         'Канал Структурирования / Гения-Фрика'),
    (24, 61, 'ajna',         'head',         'Канал Осознанности / Думания'),
    (25, 51, 'g_center',     'heart',        'Канал Признания / Инициации'),
    (26, 44, 'heart',        'spleen',       'Канал Сдачи / Передачи'),
    (27, 50, 'sacral',       'spleen',       'Канал Сохранения / Защиты'),
    (28, 38, 'spleen',       'root',         'Канал Борьбы / Упорства'),
    (29, 46, 'sacral',       'g_center',     'Канал Открытия / Обнаружения'),
    (30, 41, 'solar_plexus', 'root',         'Канал Мечтателя / Распознавания'),
    (34, 57, 'sacral',       'spleen',       'Канал Силы / Архетипа'),
    (35, 36, 'throat',       'solar_plexus', 'Канал Преходящести / Эмоций'),
    (37, 40, 'solar_plexus', 'heart',        'Канал Общины / Семьи'),
    (39, 55, 'root',         'solar_plexus', 'Канал Эмоциональности / Провокации'),
    (42, 53, 'sacral',       'root',         'Канал Созревания / Циклов'),
    (47, 64, 'ajna',         'head',         'Канал Абстрагирования / Осознания'),
]
assert len(CHANNELS) == 32, f"Expected 32 channels, got {len(CHANNELS)}"

# Быстрый поиск канала по воротам: {frozenset(g1,g2): channel_tuple}
CHANNEL_MAP: Dict[frozenset, Tuple[int, int, str, str, str]] = {}
for _ch in CHANNELS:
    CHANNEL_MAP[frozenset((_ch[0], _ch[1]))] = _ch

# ════════════════════════════════════════════════════════════════
#  ЧАСОВЫЕ ПОЯСА — основные города России и СНГ
# ════════════════════════════════════════════════════════════════

TIMEZONE_OFFSETS: Dict[str, float] = {
    # UTC+2
    'калининград': 2, 'kaliningrad': 2,
    # UTC+3 — основные города
    'москва': 3, 'moscow': 3,
    'санкт-петербург': 3, 'saint petersburg': 3, 'питер': 3, 'спб': 3,
    'казань': 3, 'kazan': 3,
    'ростов-на-дону': 3, 'ростов': 3, 'rostov-on-don': 3, 'rostov': 3,
    'воронеж': 3, 'voronezh': 3,
    'краснодар': 3, 'krasnodar': 3,
    'мурманск': 3, 'murmansk': 3,
    'волгоград': 3, 'volgograd': 3,
    'рязань': 3, 'ryazan': 3,
    'ярославль': 3, 'yaroslavl': 3,
    'тула': 3, 'tula': 3,
    'липецк': 3, 'lipetsk': 3,
    'курск': 3, 'kursk': 3,
    'смоленск': 3, 'smolensk': 3,
    'тверь': 3, 'tver': 3,
    'нижний новгород': 3, 'nizhny novgorod': 3, 'нижний': 3,
    'сочи': 3, 'sochi': 3,
    'ставрополь': 3, 'stavropol': 3,
    'белгород': 3, 'belgorod': 3,
    'владимир': 3, 'vladimir': 3,
    'иваново': 3, 'ivanovo': 3,
    'калуга': 3, 'kaluga': 3,
    'кострома': 3, 'kostroma': 3,
    'орёл': 3, 'orel': 3,
    'пенза': 3, 'penza': 3,
    'саранск': 3, 'saransk': 3,
    'тамбов': 3, 'tambov': 3,
    # UTC+3 — южные республики
    'майкоп': 3, 'maykop': 3,
    'назрань': 3, 'nazran': 3,
    'владикавказ': 3, 'vladikavkaz': 3,
    'грозный': 3, 'grozny': 3,
    'махачкала': 3, 'makhachkala': 3,
    'нальчик': 3, 'nalchik': 3,
    'черкесск': 3, 'cherkessk': 3,
    'элиста': 3, 'elista': 3,
    'астрахань': 3, 'astrakhan': 3,
    # UTC+3 — поволжье и север
    'киров': 3, 'kirov': 3,
    'йошкар-ола': 3, 'yoshkar-ola': 3,
    'чебоксары': 3, 'cheboksary': 3,
    'великий новгород': 3, 'veliky novgorod': 3,
    'петрозаводск': 3, 'petrozavodsk': 3,
    'сыктывкар': 3, 'syktyvkar': 3,
    'брянск': 3, 'bryansk': 3,
    # UTC+3 — московская область
    'дзержинск': 3, 'dzerzhinsk': 3,
    'подольск': 3, 'podolsk': 3,
    'люберцы': 3, 'lyubertsy': 3,
    'мытищи': 3, 'mytishchi': 3,
    'химки': 3, 'khimki': 3,
    'красногорск': 3, 'krasnogorsk': 3,
    'коломна': 3, 'kolomna': 3,
    'одинцово': 3, 'odintsovo': 3,
    'королёв': 3, 'korolev': 3,
    'орехово-зуево': 3, 'orekhovo-zuyevo': 3,
    'серпухов': 3, 'serpukhov': 3,
    'клин': 3, 'klin': 3,
    'раменское': 3, 'ramenskoye': 3,
    'реутов': 3, 'reutov': 3,
    'щёлково': 3, 'shchyolkovo': 3,
    'электросталь': 3, 'elektrostal': 3,
    'долгопрудный': 3, 'dolgoprudny': 3,
    'жуковский': 3, 'zhukovsky': 3,
    # UTC+3 — пригороды СПб
    'ломоносов': 3, 'lymonosov': 3,
    'пушкин': 3, 'pushkin': 3,
    'петергоф': 3, 'peterhof': 3,
    # UTC+3 — черноморское побережье
    'новороссийск': 3, 'novorossiysk': 3,
    'анапа': 3, 'anapa': 3,
    'геленджик': 3, 'gelendzhik': 3,
    # UTC+3 → UTC+4 переход
    'ульяновск': 4, 'ulyanovsk': 4,
    # UTC+4 — основные города
    'самара': 4, 'samara': 4,
    'тольятти': 4, 'tolyatti': 4,
    'ижевск': 4, 'izhevsk': 4,
    'саратов': 4, 'saratov': 4,
    # UTC+4 — самарская область
    'сызрань': 4, 'syzran': 4,
    'новокуйбышевск': 4, 'novokuybyshevsk': 4,
    'чапаевск': 4, 'chapaevsk': 4,
    # UTC+4 — башкортостан
    'октябрьский': 4, 'oktyabrsky': 4,
    'нефтекамск': 4, 'neftekamsk': 4,
    'салават': 4, 'salavat': 4,
    'ишимбай': 4, 'ishimbay': 4,
    'кумертау': 4, 'kumertau': 4,
    # UTC+5 — основные города
    'екатеринбург': 5, 'yekaterinburg': 5, 'екб': 5,
    'уфа': 5, 'ufa': 5,
    'челябинск': 5, 'chelyabinsk': 5,
    'тюмень': 5, 'tyumen': 5,
    'пермь': 5, 'perm': 5,
    'оренбург': 5, 'orenburg': 5,
    'магнитогорск': 5, 'magnitogorsk': 5,
    # UTC+5 — хмао
    'нижневартовск': 5, 'nizhnevartovsk': 5,
    'сургут': 5, 'surgut': 5,
    'нефтеюганск': 5, 'nefteyugansk': 5,
    'нягань': 5, 'nyagan': 5,
    # UTC+6 — Россия
    'омск': 6, 'omsk': 6,
    # UTC+6 — Казахстан
    'петропавловск': 6, 'petropavlovsk': 6,
    'костанай': 6, 'kostanay': 6,
    'павлодар': 6, 'pavlodar': 6,
    'семей': 6, 'semey': 6,
    'туркестан': 6, 'turkestan': 6,
    # UTC+7 — основные города
    'новосибирск': 7, 'novosibirsk': 7,
    'красноярск': 7, 'krasnoyarsk': 7,
    'барнаул': 7, 'barnaul': 7,
    'кемерово': 7, 'kemerovo': 7,
    'томск': 7, 'tomsk': 7,
    # UTC+7 — хакасия, алтай, тува
    'абакан': 7, 'abakan': 7,
    'горно-алтайск': 7, 'gorno-altaysk': 7,
    'кызыл': 7, 'kyzyl': 7,
    'норильск': 7, 'norilsk': 7,
    'минусинск': 7, 'minusinsk': 7,
    # UTC+8 — основные города
    'иркутск': 8, 'irkutsk': 8,
    'братск': 8, 'bratsk': 8,
    'ulan-ude': 8, 'улан-удэ': 8,
    # UTC+8 — амурская область
    'благовещенск': 8, 'blagoveshchensk': 8,
    'белогорск': 8, 'belogorsk': 8,
    'свободный': 8, 'svobodny': 8,
    # UTC+9
    'якутск': 9, 'yakutsk': 9,
    'чита': 9, 'chita': 9,
    # UTC+10 — основные города
    'владивосток': 10, 'vladivostok': 10,
    'хабаровск': 10, 'khabarovsk': 10,
    # UTC+10 — приморье и еао
    'биробиджан': 10, 'birobidzhan': 10,
    'находка': 10, 'nakhodka': 10,
    'уссурийск': 10, 'ussuriysk': 10,
    'арсеньев': 10, 'arsenyev': 10,
    'комсомольск-на-амуре': 10, 'komsomolsk-on-amur': 10,
    # UTC+11 — основные города
    'магадан': 11, 'magadan': 11,
    # UTC+11 — сахалин и колыма
    'южно-сахалинск': 11, 'yuzhno-sakhalinsk': 11,
    'охотск': 11, 'okhotsk': 11,
    'среднеколымск': 11, 'srednekolymsk': 11,
    # UTC+12
    'петропавловск-камчатский': 12, 'petropavlovsk-kamchatsky': 12,
    'анадырь': 12, 'anadyr': 12,
    # СНГ
    'минск': 3, 'minsk': 3,
    'киев': 2, 'kyiv': 2, 'kiev': 2,
    'алматы': 6, 'almaty': 6,
    'астана': 6, 'astana': 6,
    'ташкент': 5, 'tashkent': 5,
    'бишкек': 6, 'bishkek': 6,
    'тбилиси': 4, 'tbilisi': 4,
    'ереван': 4, 'yerevan': 4,
    'баку': 4, 'baku': 4,
    # Казахстан — запад (UTC+5)
    'актау': 5, 'aktau': 5,
    'актобе': 5, 'aktobe': 5,
    'актюбинск': 5, 'aktyubinsk': 5,
    'уральск': 5, 'uralsk': 5,
}

# ════════════════════════════════════════════════════════════════
#  ИНКАРНАЦИОННЫЕ КРЕСТЫ — названия по воротам Солнца Личности
# ════════════════════════════════════════════════════════════════

CROSS_NAMES_RU: Dict[int, str] = {
     1: 'Проникновения',      2: 'Направления',       3: 'Упорядочивания',
     4: 'Формулирования',     5: 'Ритма',             6: 'Конфликта',
     7: 'Направления (Армия)',8: 'Вклада',            9: 'Фокуса',
    10: 'Поведенческого',    11: 'Идей',             12: 'Слова',
    13: 'Слушания',          14: 'Служения',         15: 'Крайностей',
    16: 'Навыков',           17: 'Мнений',           18: 'Исправления',
    19: 'Чувствительности',  20: 'Индивидуальности',  21: 'Контроля',
    22: 'Открытости',        23: 'Ассимиляции',      24: 'Рационализации',
    25: 'Любви',             26: 'Эгоиста',          27: 'Заботы',
    28: 'Игрока',            29: 'Обязательств',     30: 'Желания',
    31: 'Лидерства',         32: 'Непрерывности',    33: 'Уединения',
    34: 'Неожиданного',      35: 'Опыта',            36: 'Кризиса',
    37: 'Общины',            38: 'Противостояния',   39: 'Провокации',
    40: 'Преданности',       41: 'Сжатия',           42: 'Роста',
    43: 'Проницательности',  44: 'Бдительности',     45: 'Собирателя',
    46: 'Случайности',       47: 'Угнетения',        48: 'Глубины',
    49: 'Революции',         50: 'Ценностей',        51: 'Пробуждения',
    52: 'Неподвижности',     53: 'Нового Начала',    54: 'Амбиций',
    55: 'Духа',              56: 'Стимуляции',       57: 'Интуиции',
    58: 'Жизненности',       59: 'Сексуальности',    60: 'Ограничения',
    61: 'Внутренней Истины', 62: 'Деталей',          63: 'Сомнения',
    64: 'Многообразия',
}

# ════════════════════════════════════════════════════════════════
#  СВОЙСТВА ВОРОТ (для контекста)
# ════════════════════════════════════════════════════════════════

GATE_KEYWORDS_RU: Dict[int, str] = {
     1: 'Творчество / Самовыражение',       2: 'Направление / Высшее Я',
     3: 'Порядок / Мутация',                4: 'Формулирование / Ответы',
     5: 'Ритм / Паттерны',                  6: 'Конфликт / Интимность',
     7: 'Армия / Роль Я',                   8: 'Вклад / Самовыражение',
     9: 'Фокус / Энергия',                 10: 'Поведение / Любовь к себе',
    11: 'Идеи / Мир',                      12: 'Осторожность / Слово',
    13: 'Слушание / Тайны',                14: 'Достояние / Служение',
    15: 'Крайности / Магнетизм',           16: 'Навыки / Способности',
    17: 'Мнения / Логика',                 18: 'Исправление / Ошибки',
    19: 'Чувствительность / Подход',       20: 'Настоящее / Присутствие',
    21: 'Контроль / Охота',               22: 'Грация / Открытость',
    23: 'Ассимиляция / Принятие',          24: 'Рационализация / Возвращение',
    25: 'Любовь / Невинность',             26: 'Эгоизм / Кумовство',
    27: 'Забота / Питание',               28: 'Рискованность / Игра',
    29: 'Обязательства / «Да»',            30: 'Желание / Чувства',
    31: 'Влияние / Лидерство',             32: 'Непрерывность / Трансформация',
    33: 'Уединение / Тайна',              34: 'Сила / Мощь',
    35: 'Прогресс / Изменение',            36: 'Кризис / Переход',
    37: 'Дружба / Семья',                 38: 'Оппозиция / Борьба',
    39: 'Провокация / Освобождение',       40: 'Одиночество / Преданность',
    41: 'Сжатие / Начало',                42: 'Рост / Завершение',
    43: 'Проницательность / Внутренний слух', 44: 'Бдительность / Внимание',
    45: 'Собирание / Власть',             46: 'Счастье / Тело',
    47: 'Угнетение / Осознание',          48: 'Глубина / Неудовлетворённость',
    49: 'Революция / Принципы',           50: 'Ценности / Космический порядок',
    51: 'Пробуждение / Шок',              52: 'Неподвижность / Медитация',
    53: 'Новое начало / Инициация',       54: 'Амбиции / Движение вверх',
    55: 'Дух / Эмоциональность',          56: 'Стимуляция / История',
    57: 'Интуиция / Ясное чутьё',         58: 'Жизненность / Радость',
    59: 'Сексуальность / Прорыв',         60: 'Ограничение / Принятие',
    61: 'Внутренняя истина / Тайна',      62: 'Детали / Логика',
    63: 'Сомнение / Проверка',            64: 'Многообразие / Замешательство',
}


# ════════════════════════════════════════════════════════════════
#  УТИЛИТЫ — ЮЛИАНСКИЙ ДЕНЬ, ЧАСОВЫЕ ПОЯСА
# ════════════════════════════════════════════════════════════════

def _parse_birth_time(birth_time_str: str) -> Tuple[int, int]:
    """Разобрать строку времени рождения 'HH:MM' или 'HH:MM Moscow'.

    Возвращает (часы, минуты).
    """
    s = birth_time_str.strip()
    # Убираем город/пояс после времени
    time_part = s.split()[0] if ' ' in s else s
    # Разные разделители
    for sep in ':hH.':
        if sep in time_part:
            parts = time_part.split(sep)
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
    # Fallback — первые 2 и следующие 2 цифры
    digits = ''.join(c for c in time_part if c.isdigit())
    if len(digits) >= 3:
        return int(digits[:2]), int(digits[2:4]) if len(digits) >= 4 else 0
    return 12, 0  # полдень по умолчанию


def _get_timezone_offset(birth_place: str) -> float:
    """Определить смещение часового пояса по названию города.

    Возвращает смещение в часах от UTC (положительное = восток).
    """
    if not birth_place:
        return 3.0  # Москва по умолчанию
    place = birth_place.strip().lower()
    # Точная проверка
    if place in TIMEZONE_OFFSETS:
        return float(TIMEZONE_OFFSETS[place])
    # Частичное совпадение
    for key, offset in TIMEZONE_OFFSETS.items():
        if key in place or place in key:
            return float(offset)
    # UTC+N в тексте
    import re
    m = re.search(r'UTC([+-]?\d+)', place, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r'GMT([+-]?\d+)', place, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # Fallback
    logger.warning("Не удалось определить пояс для '%s', используем UTC+3", birth_place)
    return 3.0


def _datetime_to_jd(dt: datetime) -> float:
    """Перевести datetime в Юлианский день."""
    if _SWE_AVAILABLE:
        # pyswisseph: swe.julday(year, month, day, hour_ut)
        hour_ut = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
        return swe.julday(dt.year, dt.month, dt.day, hour_ut)
    else:
        return _julian_day_fallback(dt)


def _julian_day_fallback(dt: datetime) -> float:
    """Расчёт Юлианского дня без pyswisseph (алгоритм Мёйса)."""
    y = dt.year
    m = dt.month
    d = dt.day + dt.hour / 24.0 + dt.minute / 1440.0 + dt.second / 86400.0
    if m <= 2:
        y -= 1
        m += 12
    A = int(y / 100)
    B = 2 - A + int(A / 4)
    JD = int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + B - 1524.5
    return JD


# ════════════════════════════════════════════════════════════════
#  1. ПЛАНЕТНЫЕ ПОЗИЦИИ — РАСЧЁТ
# ════════════════════════════════════════════════════════════════

# Идентификаторы планет в Swiss Ephemeris
_SWE_BODIES = {
    'sun':         0,   # swe.SUN
    'moon':        1,   # swe.MOON
    'mercury':     2,   # swe.MERCURY
    'venus':       3,   # swe.VENUS
    'mars':        4,   # swe.MARS
    'jupiter':     5,   # swe.JUPITER
    'saturn':      6,   # swe.SATURN
    'uranus':      7,   # swe.URANUS
    'neptune':     8,   # swe.NEPTUNE
    'pluto':       9,   # swe.PLUTO
    'north_node':  10,  # swe.MEAN_NODE (Раху)
    'south_node':  -1,  # Вычисляется как North Node + 180°
    'chiron':      15,  # swe.CHIRON
}

PLANET_NAMES_RU = {
    'sun':         'Солнце',
    'moon':        'Луна',
    'mercury':     'Меркурий',
    'venus':       'Венера',
    'mars':        'Марс',
    'jupiter':     'Юпитер',
    'saturn':      'Сатурн',
    'uranus':      'Уран',
    'neptune':     'Нептун',
    'pluto':       'Плутон',
    'north_node':  'Северный Узел (Раху)',
    'south_node':  'Южный Узел (Кету)',
    'chiron':      'Хирон',
    'earth':       'Земля',
}

# Тела для которых считаем позицию (кроме Земли — она всегда оппозит Солнцу)
_CALC_BODIES = [
    'sun', 'moon', 'mercury', 'venus', 'mars',
    'jupiter', 'saturn', 'uranus', 'neptune', 'pluto',
    'north_node', 'chiron',
]


def _calc_planet_swe(jd: float, body_name: str) -> float:
    """Рассчитать эклиптическую долготу планеты через Swiss Ephemeris.

    Возвращает долготу в градусах [0, 360).
    Сначала пробует Swiss Ephemeris (нужны файлы), затем Moshier (встроенный).
    """
    body_id = _SWE_BODIES.get(body_name)
    if body_id is None:
        return 0.0
    # Сначала пробуем Swiss Ephemeris (самый точный)
    for flags in [
        swe.FLG_SWIEPH,       # Точный, нужны файлы .se1
        swe.FLG_MOSEPH,       # Встроенный Moshier, без файлов
    ]:
        try:
            result = swe.calc_ut(jd, body_id, flags)
            lon = result[0][0]  # эклиптическая долгота
            return lon % 360.0
        except Exception as e:
            logger.debug("swe.calc_ut flag=%d error for %s: %s", flags, body_name, e)
            continue
    # Всё не удалось — fallback (обычно Chiron, т.к. Moshier его не содержит)
    logger.debug("SwissEph failed for %s, using orbital fallback", body_name)
    return _planet_longitude_approx(jd, body_name)


def _calc_all_planets_swe(jd: float) -> Dict[str, float]:
    """Рассчитать все планетные позиции через Swiss Ephemeris."""
    positions = {}
    for body in _CALC_BODIES:
        positions[body] = _calc_planet_swe(jd, body)
    # Южный узел = Северный + 180°
    positions['south_node'] = (positions.get('north_node', 0) + 180.0) % 360.0
    # Земля = Солнце + 180°
    positions['earth'] = (positions.get('sun', 0) + 180.0) % 360.0
    return positions


# ────────────────────────────────────────────────────────────────
#  УПРОЩЁННЫЙ РАСЧЁТ (fallback без pyswisseph)
# ────────────────────────────────────────────────────────────────

def _deg_norm(d: float) -> float:
    """Нормализовать угол в [0, 360)."""
    return d % 360.0


def _sun_longitude(jd: float) -> float:
    """Аппроксимация долготы Солнца (алгоритм Meeus, гл. 25)."""
    T = (jd - 2451545.0) / 36525.0
    L0 = _deg_norm(280.46646 + 36000.76983 * T + 0.0003032 * T * T)
    M = _deg_norm(357.52911 + 35999.05029 * T - 0.0001537 * T * T)
    Mr = math.radians(M)
    e = 0.016708634 - 0.000042037 * T - 0.0000001267 * T * T
    C = ((1.914602 - 0.004817 * T - 0.000014 * T * T) * math.sin(Mr)
         + (0.019993 - 0.000101 * T) * math.sin(2 * Mr)
         + 0.000289 * math.sin(3 * Mr))
    sun_true = L0 + C
    omega = math.radians(125.04 - 1934.136 * T)
    lon = sun_true - 0.00569 - 0.00478 * math.sin(omega)
    return _deg_norm(lon)


def _moon_longitude(jd: float) -> float:
    """Аппроксимация долготы Луны (упрощённый алгоритм Meeus, гл. 47)."""
    T = (jd - 2451545.0) / 36525.0
    Lp = _deg_norm(218.3165 + 481267.8813 * T)
    M = _deg_norm(134.9634 + 477198.8676 * T)
    Mp = _deg_norm(93.2720 + 483202.0175 * T)
    D = _deg_norm(297.8502 + 445267.1115 * T)
    F = _deg_norm(125.0446 - 1934.1366 * T)
    Mr = math.radians(M)
    Mpr = math.radians(Mp)
    Dr = math.radians(D)
    Fr = math.radians(F)
    lon = (Lp
           + 6.289 * math.sin(Mr)
           + 1.274 * math.sin(2 * Dr - Mr)
           + 0.658 * math.sin(2 * Dr)
           + 0.214 * math.sin(2 * Mr)
           - 0.186 * math.sin(Mr + Mpr)
           - 0.114 * math.sin(2 * Fr))
    return _deg_norm(lon)


def _mean_node_longitude(jd: float) -> float:
    """Средняя долгота Северного Лунного Узла."""
    T = (jd - 2451545.0) / 36525.0
    lon = 125.0446 - 1934.1366 * T + 0.0021 * T * T
    return _deg_norm(lon)


# Средние орбитальные элементы для аппроксимации (J2000.0)
# (L0 — средняя долгота, Ld — среднее суточное движение °/день, e — эксцентриситет,
#  omega — долгота перигелия, Omega — долгота восходящего узла, i — наклон)
_PLANET_ORBITAL = {
    'mercury':  (252.2509, 4.09233445, 0.205636,  77.4561,  48.3313, 7.005),
    'venus':    (181.9798, 1.60213034, 0.006770, 131.5637,  76.6807, 3.395),
    'mars':     (355.4330, 0.52402068, 0.093401, 336.0402,  49.5581, 1.850),
    'jupiter':  ( 34.3515, 0.08308529, 0.048382,  14.3312, 100.4644, 1.303),
    'saturn':   ( 50.0774, 0.03344414, 0.053863,  93.0572, 113.6655, 2.489),
    'uranus':   (314.0550, 0.01172834, 0.047257, 173.0053,  74.0060, 0.773),
    'neptune':  (304.3487, 0.00598103, 0.008590,  48.1203, 131.7841, 1.770),
    'pluto':    (238.9288, 0.00396486, 0.248808, 224.0669, 110.2990, 17.16),
    'chiron':   (209.0,    0.0038,     0.383,     339.0,    209.0,    6.9),
}


def _planet_longitude_approx(jd: float, body_name: str) -> float:
    """Аппроксимация долготы планеты через средние орбитальные элементы.

    Точность ±2-5° — достаточно для определения ворот (5.625° на ворот).
    """
    if body_name == 'sun':
        return _sun_longitude(jd)
    if body_name == 'moon':
        return _moon_longitude(jd)
    if body_name == 'north_node':
        return _mean_node_longitude(jd)

    orb = _PLANET_ORBITAL.get(body_name)
    if orb is None:
        return 0.0
    L0, Ld, e, omega, Omega, i_deg = orb

    # Количество дней от J2000.0
    d = jd - 2451545.0
    # Средняя долгота
    L = L0 + Ld * d
    # Средняя аномалия
    M = L - omega
    Mr = math.radians(M % 360.0)

    # Уравнение центра (1-й и 2-й порядок)
    C = ((2 * e - 0.25 * e * e) * math.sin(Mr)
         + 1.25 * e * e * math.sin(2 * Mr)
         + (13.0 / 12.0) * e * e * e * math.sin(3 * Mr))

    lon = L + C
    return _deg_norm(lon)


def _calc_all_planets_fallback(jd: float) -> Dict[str, float]:
    """Рассчитать все планетные позиции упрощённым методом."""
    positions = {}
    for body in _CALC_BODIES:
        positions[body] = _planet_longitude_approx(jd, body)
    positions['south_node'] = (positions.get('north_node', 0) + 180.0) % 360.0
    positions['earth'] = (positions.get('sun', 0) + 180.0) % 360.0
    return positions


def calc_all_planets(jd: float) -> Dict[str, float]:
    """Рассчитать все планетные позиции (лучший доступный метод).

    Возвращает словарь {имя_планеты: долгота_в_градусах}.
    """
    if _SWE_AVAILABLE:
        return _calc_all_planets_swe(jd)
    return _calc_all_planets_fallback(jd)


# ════════════════════════════════════════════════════════════════
#  ДИЗАЙН-ДАТА — Солнце на 88° раньше
# ════════════════════════════════════════════════════════════════

def _find_design_jd(birth_jd: float) -> float:
    """Найти Юлианский день Дизайна: когда Солнце было на 88° раньше.

    Солнце движется ~0.9856°/день, поэтому 88° ≈ 89.3 дня.
    Но мы ищем ТОЧНУЮ дату итеративно.
    """
    # Целевая долгота Солнца Дизайна
    birth_sun = calc_all_planets(birth_jd)['sun']
    target_lon = (birth_sun - 88.0) % 360.0

    # Первое приближение: ~88 дней до рождения
    # Солнце движется ~0.9856°/день
    approx_days_back = 88.0 / 0.9856
    design_jd = birth_jd - approx_days_back

    # Итеративное уточнение (Ньютон-Рафсон)
    for _ in range(20):
        design_sun = calc_all_planets(design_jd)['sun']
        diff = (design_sun - target_lon + 180.0) % 360.0 - 180.0  # [-180, +180]
        if abs(diff) < 0.0001:
            break
        # Поправка: diff градусов → diff / 0.9856 дней
        design_jd -= diff / 0.9856

    return design_jd


# ════════════════════════════════════════════════════════════════
#  2. МАППИНГ ДОЛГОТЫ → ВОРОТА + ЛИНИЯ
# ════════════════════════════════════════════════════════════════

def longitude_to_gate_line(lon: float) -> Tuple[int, int, float]:
    """Перевести эклиптическую долготу в номер ворот и линию.

    Возвращает (gate, line, position_in_gate).
    gate: 1-64
    line: 1-6
    position_in_gate: 0.0-5.625 (градусы внутри ворот)
    """
    lon = lon % 360.0
    sector = int(lon / GATE_SIZE)  # 0..63
    if sector >= 64:
        sector = 63
    gate = GATE_SEQUENCE[sector]
    position_in_gate = lon - sector * GATE_SIZE
    # Линия: каждая = 0.9375° (5.625 / 6)
    line = int(position_in_gate / (GATE_SIZE / 6.0)) + 1
    if line > 6:
        line = 6
    return gate, line, position_in_gate


def longitude_to_gate(lon: float) -> int:
    """Только номер ворот по долготе."""
    gate, _, _ = longitude_to_gate_line(lon)
    return gate


# ════════════════════════════════════════════════════════════════
#  5. ОПРЕДЕЛЁННОСТЬ ЦЕНТРОВ
# ════════════════════════════════════════════════════════════════

def _determine_centers_and_channels(
    personality_gates: Set[int],
    design_gates: Set[int],
) -> Tuple[Set[str], List[Tuple[int, int, str, str, str]], Set[int]]:
    """Определить какие центры определены и какие канала полные.

    personality_gates: множество активированных ворот Личности
    design_gates: множество активированных ворот Дизайна

    Возвращает:
      - defined_centers: множество определённых центров
      - complete_channels: список полных каналов
      - all_activated_gates: все активированные вороты
    """
    all_activated = personality_gates | design_gates

    # Находим полные каналы (оба вороты активированы)
    complete_channels = []
    for ch in CHANNELS:
        g1, g2 = ch[0], ch[1]
        if g1 in all_activated and g2 in all_activated:
            complete_channels.append(ch)

    # Строим граф центров через полные каналы
    # Центр определён, если он соединён через полные каналы с другим определённым центром
    # Используем BFS/DFS для поиска компонент связности

    # Сначала: центры, которые имеют хотя бы одно полное соединение
    center_graph: Dict[str, Set[str]] = {c: set() for c in CENTER_NAMES_RU}
    for ch in complete_channels:
        c1, c2 = ch[2], ch[3]
        center_graph[c1].add(c2)
        center_graph[c2].add(c1)

    # Центр определён если он входит в любую компоненту связности размера >= 2
    # (т.е. соединён хотя бы с одним другим центром через канал)
    visited: Set[str] = set()
    defined_centers: Set[str] = set()

    for center in CENTER_NAMES_RU:
        if center in visited:
            continue
        # BFS
        component: Set[str] = set()
        queue = [center]
        while queue:
            node = queue.pop(0)
            if node in component:
                continue
            component.add(node)
            for neighbor in center_graph.get(node, set()):
                if neighbor not in component:
                    queue.append(neighbor)
        visited |= component
        # Компонента из >1 центра → все центры в ней определены
        if len(component) > 1:
            defined_centers |= component

    return defined_centers, complete_channels, all_activated


# ════════════════════════════════════════════════════════════════
#  6. ТИП
# ════════════════════════════════════════════════════════════════

def _determine_type(
    defined_centers: Set[str],
    complete_channels: List[Tuple[int, int, str, str, str]],
) -> str:
    """Определить Тип Дизайна Человека.

    Возвращает одно из:
      'Генератор', 'Манифестирующий Генератор', 'Проектор', 'Манифестор', 'Рефлектор'
    """
    sacral_defined = 'sacral' in defined_centers
    throat_defined = 'throat' in defined_centers

    # Проверяем связь Горла с мотором через канал
    throat_connected_to_motor = False
    throat_connected_to_sacral_via_motor = False

    for ch in complete_channels:
        c1, c2 = ch[2], ch[3]
        # Горло напрямую соединено с мотором?
        if c1 == 'throat' and c2 in MOTOR_CENTERS:
            throat_connected_to_motor = True
            if c2 != 'sacral':
                # Мотор (не сакральный) → горло, и сакральный определён
                if sacral_defined:
                    throat_connected_to_sacral_via_motor = True
        elif c2 == 'throat' and c1 in MOTOR_CENTERS:
            throat_connected_to_motor = True
            if c1 != 'sacral':
                if sacral_defined:
                    throat_connected_to_sacral_via_motor = True

    # Также проверяем: сакральный → мотор → горло (косвенная связь)
    if sacral_defined and throat_connected_to_motor:
        throat_connected_to_sacral_via_motor = True

    # Рефлектор: ни один центр не определён
    if not defined_centers:
        return 'Рефлектор'

    # Генераторы (сакральный определён)
    if sacral_defined:
        if throat_connected_to_sacral_via_motor or throat_connected_to_motor:
            return 'Манифестирующий Генератор'
        else:
            return 'Генератор'

    # Не-сакральные типы
    # Манифестор: горло соединено с мотором, сакральный НЕ определён
    if throat_connected_to_motor:
        return 'Манифестор'

    # Проектор: всё остальное (нет сакрального, нет мотор→горло)
    return 'Проектор'


# ════════════════════════════════════════════════════════════════
#  7. АВТОРИТЕТ
# ════════════════════════════════════════════════════════════════

def _determine_authority(
    defined_centers: Set[str],
    hd_type: str,
) -> str:
    """Определить Внутренний Авторитет."""
    if 'solar_plexus' in defined_centers:
        return 'Эмоциональный'
    if 'sacral' in defined_centers:
        return 'Сакральный'
    if 'spleen' in defined_centers:
        return 'Селезёнки (Спонтанный)'
    if 'heart' in defined_centers:
        return 'Эго (Волевой)'
    if 'g_center' in defined_centers:
        return 'Самопроектируемый'
    if 'ajna' in defined_centers:
        return 'Ментальный (Внешний)'
    # Нет моторов / центров осознания
    if hd_type == 'Рефлектор':
        return 'Лунный'
    return 'Ментальный (Внешний)'  # для Проекторов без моторов


# ════════════════════════════════════════════════════════════════
#  8. ПРОФИЛЬ
# ════════════════════════════════════════════════════════════════

def _determine_profile(personality_sun_line: int, design_sun_line: int) -> str:
    """Профиль = Линия Солнца Личности / Линия Солнца Дизайна.

    Возвращает строку типа '4/6'.
    """
    return f"{personality_sun_line}/{design_sun_line}"


PROFILE_DESCRIPTIONS_RU: Dict[str, str] = {
    '1/3': 'Исследователь/Мученик — глубина исследования через пробы и ошибки',
    '1/4': 'Исследователь/Оппортунист — глубина исследования через сеть контактов',
    '2/4': 'Отшельник/Оппортунист — природный талант, выявляемый через других',
    '2/5': 'Отшельник/Еретик — природный талант, проецируемый на других',
    '3/5': 'Мученик/Еретик — обучение через опыт и ошибки, практическая универсальность',
    '3/6': 'Мученик/Ролевая модель — обучение через опыт, эволюция к мудрости',
    '4/6': 'Оппортунист/Ролевая модель — сеть контактов, эволюция к мудрости',
    '4/1': 'Оппортунист/Исследователь — фиксированная судьба, глубина через контакты',
    '5/1': 'Еретик/Исследователь — практическое лидерство через глубину',
    '5/2': 'Еретик/Отшельник — практическое лидерство, природный дар',
    '6/2': 'Ролевая модель/Отшельник — трёхфазная жизнь, объективность',
    '6/3': 'Ролевая модель/Мученик — трёхфазная жизнь, обучение через опыт',
}


# ════════════════════════════════════════════════════════════════
#  9. ПЕРЕМЕННЫЕ (4 стрелки) — Цвет, Тон, Пищеварение, Среда,
#     Перспектива, Мотивация
# ════════════════════════════════════════════════════════════════

# ─── Константы для Цветов и Тонов ───

# Каждый ворот = 5.625°, каждая линия = 0.9375°
# Каждый цвет внутри линии = 0.15625° (0.9375 / 6)
# Каждый тон внутри цвета ≈ 0.02604° (0.15625 / 6)

LINE_WIDTH = GATE_SIZE / 6.0    # 0.9375°
COLOR_WIDTH = LINE_WIDTH / 6.0  # 0.15625°
TONE_WIDTH = COLOR_WIDTH / 6.0  # ≈ 0.02604°

# Названия 6 Цветов (общие)
COLOR_NAMES: Dict[int, str] = {
    1: 'Определённый (Determined)',
    2: 'Настроенный (Adjusted)',
    3: 'Перенесённый (Transferred)',
    4: 'Открытый (Open)',
    5: 'Перекрёстный (Crossed)',
    6: 'Динамичный (Dynamic)',
}

# Названия 6 Тонов
TONE_NAMES: Dict[int, str] = {
    1: 'Обоняние (Smell)',
    2: 'Вкус (Taste)',
    3: 'Внешнее зрение (Outer Vision)',
    4: 'Внутреннее зрение (Inner Vision)',
    5: 'Чувство (Feeling)',
    6: 'Осязание (Touch)',
}

# Названия Цветов для Пищеварения (Design Sun Color)
DIGESTION_COLOR_NAMES: Dict[int, str] = {
    1: 'Последовательное (Consecutive)',
    2: 'Чередующееся (Alternating)',
    3: 'Закрытое (Closed)',
    4: 'Открытое (Open)',
    5: 'Перекрёстное (Crossed)',
    6: 'Динамичное (Dynamic)',
}

# Названия Цветов для Среды (Personality Sun Color)
ENVIRONMENT_COLOR_NAMES: Dict[int, str] = {
    1: 'Пещеры (Caves)',
    2: 'Рынки (Markets)',
    3: 'Кухни (Kitchens)',
    4: 'Горы (Mountains)',
    5: 'Долины (Valleys)',
    6: 'Берега (Shores)',
}

# Названия Тонов для Перспективы (Personality Sun Tone)
PERSPECTIVE_TONE_NAMES: Dict[int, str] = {
    1: 'Обоняние — активное исследование (Smell)',
    2: 'Вкус — избирательность (Taste)',
    3: 'Внешнее зрение — фокус вовне (Outer Vision)',
    4: 'Внутреннее зрение — внутренний фокус (Inner Vision)',
    5: 'Чувство — интуитивное восприятие (Feeling)',
    6: 'Осязание — контактное восприятие (Touch)',
}

# Названия Тонов для Мотивации (Design Sun Tone)
MOTIVATION_TONE_NAMES: Dict[int, str] = {
    1: 'Обоняние — личная потребность (Smell)',
    2: 'Вкус — личный выбор (Taste)',
    3: 'Внешнее зрение — личная перспектива (Outer Vision)',
    4: 'Внутреннее зрение — трансцендентная перспектива (Inner Vision)',
    5: 'Чувство — трансцендентная мотивация (Feeling)',
    6: 'Осязание — трансцендентный контакт (Touch)',
}


# ─── Функция определения Цвета и Тона ───

def longitude_to_color_tone(lon: float) -> Tuple[int, int, int, int]:
    """Перевести эклиптическую долготу в ворота, линию, цвет и тон.

    Иерархия: Ворото → Линия → Цвет → Тон.
    Каждый ворот = 5.625°, линия = 0.9375°, цвет = 0.15625°, тон ≈ 0.02604°.

    Возвращает: (gate, line, color, tone)
        gate: 1-64
        line: 1-6
        color: 1-6
        tone: 1-6
    """
    lon = lon % 360.0
    sector = int(lon / GATE_SIZE)  # 0..63
    if sector >= 64:
        sector = 63
    gate = GATE_SEQUENCE[sector]
    position_in_gate = lon - sector * GATE_SIZE

    # Линия: каждая = 0.9375°
    line = int(position_in_gate / LINE_WIDTH) + 1
    if line > 6:
        line = 6
    position_in_line = position_in_gate - (line - 1) * LINE_WIDTH

    # Цвет: каждый = 0.15625°
    color = int(position_in_line / COLOR_WIDTH) + 1
    if color > 6:
        color = 6
    position_in_color = position_in_line - (color - 1) * COLOR_WIDTH

    # Тон: каждый ≈ 0.02604°
    tone = int(position_in_color / TONE_WIDTH) + 1
    if tone > 6:
        tone = 6

    return gate, line, color, tone


def determine_variables(
    personality_sun_lon: float,
    design_sun_lon: float,
) -> Dict[str, Any]:
    """Определить 4 Переменные Дизайна Человека на основе Цвета и Тона Солнц.

    Переменные (4 стрелки на бодиграфе):
      1. Пищеварение/Питание (верхняя левая) — Design Sun Color
         Цвет 1-3 = Левое/Активное, 4-6 = Правое/Восприимчивое
      2. Среда (верхняя правая) — Personality Sun Color
         Цвет 1-3 = Левое/Выбранное, 4-6 = Правое/Обнаруженное
      3. Перспектива/Видение (нижняя правая) — Personality Sun Tone
         Тон 1-3 = Левое/Фокусированное, 4-6 = Правое/Периферийное
      4. Мотивация (нижняя левая) — Design Sun Tone
         Тон 1-3 = Левое/Личное, 4-6 = Правое/Трансцендентное

    Параметры:
        personality_sun_lon: эклиптическая долгота Солнца Личности
        design_sun_lon: эклиптическая долгота Солнца Дизайна

    Возвращает:
        словарь с 4 переменными и их описаниями
    """
    # Определяем Цвет и Тон для обоих Солнц
    p_gate, p_line, p_color, p_tone = longitude_to_color_tone(personality_sun_lon)
    d_gate, d_line, d_color, d_tone = longitude_to_color_tone(design_sun_lon)

    # ── 1. Пищеварение (Design Sun Color) ──
    digestion_left_right = 'Left (Активное)' if d_color <= 3 else 'Right (Восприимчивое)'
    digestion_color_name = DIGESTION_COLOR_NAMES.get(d_color, '?')

    # ── 2. Среда (Personality Sun Color) ──
    environment_left_right = 'Left (Выбранная)' if p_color <= 3 else 'Right (Обнаруженная)'
    environment_color_name = ENVIRONMENT_COLOR_NAMES.get(p_color, '?')

    # ── 3. Перспектива (Personality Sun Tone) ──
    perspective_left_right = 'Left (Фокусированная)' if p_tone <= 3 else 'Right (Периферийная)'
    perspective_tone_name = PERSPECTIVE_TONE_NAMES.get(p_tone, '?')

    # ── 4. Мотивация (Design Sun Tone) ──
    motivation_left_right = 'Left (Личная)' if d_tone <= 3 else 'Right (Трансцендентная)'
    motivation_tone_name = MOTIVATION_TONE_NAMES.get(d_tone, '?')

    return {
        'digestion': {
            'arrow': 'top-left',
            'direction': digestion_left_right,
            'color': d_color,
            'color_name': digestion_color_name,
            'tone': d_tone,
            'tone_name': TONE_NAMES.get(d_tone, '?'),
            'description': (
                f'Пищеварение: {digestion_left_right}, Цвет {d_color} — {digestion_color_name}. '
                f'Тон {d_tone} — {TONE_NAMES.get(d_tone, "?")}. '
                f'Определяется Цветом Солнца Дизайна.'
            ),
        },
        'environment': {
            'arrow': 'top-right',
            'direction': environment_left_right,
            'color': p_color,
            'color_name': environment_color_name,
            'tone': p_tone,
            'tone_name': TONE_NAMES.get(p_tone, '?'),
            'description': (
                f'Среда: {environment_left_right}, Цвет {p_color} — {environment_color_name}. '
                f'Тон {p_tone} — {TONE_NAMES.get(p_tone, "?")}. '
                f'Определяется Цветом Солнца Личности.'
            ),
        },
        'perspective': {
            'arrow': 'bottom-right',
            'direction': perspective_left_right,
            'color': p_color,
            'color_name': COLOR_NAMES.get(p_color, '?'),
            'tone': p_tone,
            'tone_name': perspective_tone_name,
            'description': (
                f'Перспектива: {perspective_left_right}, Тон {p_tone} — {perspective_tone_name}. '
                f'Цвет {p_color} — {COLOR_NAMES.get(p_color, "?")}. '
                f'Определяется Тоном Солнца Личности.'
            ),
        },
        'motivation': {
            'arrow': 'bottom-left',
            'direction': motivation_left_right,
            'color': d_color,
            'color_name': COLOR_NAMES.get(d_color, '?'),
            'tone': d_tone,
            'tone_name': motivation_tone_name,
            'description': (
                f'Мотивация: {motivation_left_right}, Тон {d_tone} — {motivation_tone_name}. '
                f'Цвет {d_color} — {COLOR_NAMES.get(d_color, "?")}. '
                f'Определяется Тоном Солнца Дизайна.'
            ),
        },
        # Сводка для быстрого доступа
        'summary': {
            'digestion_direction': digestion_left_right,
            'environment_direction': environment_left_right,
            'perspective_direction': perspective_left_right,
            'motivation_direction': motivation_left_right,
            'personality_sun_color': p_color,
            'personality_sun_tone': p_tone,
            'design_sun_color': d_color,
            'design_sun_tone': d_tone,
        },
    }


# ════════════════════════════════════════════════════════════════
#  10. ИНКАРНАЦИОННЫЙ КРЕСТ
# ════════════════════════════════════════════════════════════════

def _determine_incarnation_cross(
    personality_sun_gate: int,
    personality_earth_gate: int,
    design_sun_gate: int,
    design_earth_gate: int,
    profile: str,
) -> Dict[str, str]:
    """Определить Инкарнационный Крест."""
    first_line = int(profile.split('/')[0])

    # Тип креста по линии Солнца Личности
    if first_line <= 3:
        cross_type = 'Правый Угловой'
        cross_type_en = 'Right Angle'
    elif first_line == 4:
        cross_type = 'Джукстапозиция'
        cross_type_en = 'Juxtaposition'
    else:  # 5 or 6
        cross_type = 'Левый Угловой'
        cross_type_en = 'Left Angle'

    cross_name = CROSS_NAMES_RU.get(personality_sun_gate, 'Неизвестный')

    full_name = f"{cross_type} Крест {cross_name}"

    return {
        'type': cross_type,
        'type_en': cross_type_en,
        'name': cross_name,
        'full_name': full_name,
        'gates': f"{personality_sun_gate}/{personality_earth_gate} — "
                 f"{design_sun_gate}/{design_earth_gate}",
    }


# ════════════════════════════════════════════════════════════════
#  ОПРЕДЕЛЁННОСТЬ (КОЛИЧЕСТВО ЧАСТЕЙ)
# ════════════════════════════════════════════════════════════════

def _determine_definition(defined_centers: Set[str],
                          complete_channels: List) -> str:
    """Определить тип определённости (одиночная, раздельная и т.д.)."""
    if not defined_centers:
        return 'Без определения (Рефлектор)'

    # Строим граф центров
    graph: Dict[str, Set[str]] = {c: set() for c in defined_centers}
    for ch in complete_channels:
        c1, c2 = ch[2], ch[3]
        if c1 in defined_centers and c2 in defined_centers:
            graph[c1].add(c2)
            graph[c2].add(c1)

    # Считаем компоненты связности
    visited: Set[str] = set()
    components = 0
    for center in defined_centers:
        if center in visited:
            continue
        components += 1
        queue = [center]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for nb in graph.get(node, set()):
                if nb not in visited:
                    queue.append(nb)

    names = {
        1: 'Одиночное определение',
        2: 'Раздельное определение (2 части)',
        3: 'Раздельное определение (3 части)',
        4: 'Раздельное определение (4 части)',
    }
    if components >= 5:
        return f'Раздельное определение ({components} частей)'
    return names.get(components, f'Раздельное определение ({components} частей)')


# ════════════════════════════════════════════════════════════════
#  11. ГЛАВНАЯ ФУНКЦИЯ РАСЧЁТА
# ════════════════════════════════════════════════════════════════

def calculate_bodygraph(
    day: int,
    month: int,
    year: int,
    birth_time_str: str,
    birth_place: str = '',
) -> Dict[str, Any]:
    """Полный расчёт Бодиграфа Дизайна Человека.

    Параметры:
      day: день рождения (1-31)
      month: месяц рождения (1-12)
      year: год рождения
      birth_time_str: время рождения 'HH:MM' или 'HH:MM Москва'
      birth_place: место рождения (для определения часового пояса)

    Возвращает словарь со всеми рассчитанными данными.
    """
    result: Dict[str, Any] = {
        'birth_date': f"{day:02d}.{month:02d}.{year}",
        'birth_time': birth_time_str,
        'birth_place': birth_place,
        'engine': 'pyswisseph' if _SWE_AVAILABLE else 'fallback_orbital',
        'error': None,
    }

    try:
        # ── Парсинг времени и пояс ──
        hour, minute = _parse_birth_time(birth_time_str)
        tz_offset = _get_timezone_offset(birth_place)

        # UTC datetime
        birth_dt = datetime(year, month, day, hour, minute, 0)
        # Конвертируем в UTC: вычитаем пояс
        birth_utc = birth_dt - timedelta(hours=tz_offset)

        # ── Юлианский день рождения ──
        birth_jd = _datetime_to_jd(birth_utc)
        result['birth_jd'] = birth_jd
        result['birth_utc'] = birth_utc.isoformat()

        # ── Инициализация Swiss Ephemeris ──
        if _SWE_AVAILABLE:
            try:
                swe.set_ephe_path('')  # использовать встроенные данные
            except Exception:
                pass

        # ── Позиции Личности (рождение) ──
        personality_positions = calc_all_planets(birth_jd)
        result['personality_positions'] = personality_positions

        # ── Дизайн-дата ──
        design_jd = _find_design_jd(birth_jd)
        design_dt_utc = datetime(2000, 1, 1) + timedelta(days=(design_jd - 2451545.0))
        result['design_jd'] = design_jd
        result['design_date_utc'] = design_dt_utc.strftime('%d.%m.%Y %H:%M')

        # ── Позиции Дизайна ──
        design_positions = calc_all_planets(design_jd)
        result['design_positions'] = design_positions

        # ── Маппинг на вороты и линии ──
        personality_gates: Dict[str, Dict] = {}
        design_gates: Dict[str, Dict] = {}
        personality_gate_set: Set[int] = set()
        design_gate_set: Set[int] = set()

        for body_name in list(_CALC_BODIES) + ['earth', 'south_node']:
            # Личность
            if body_name in personality_positions:
                lon = personality_positions[body_name]
                gate, line, pos = longitude_to_gate_line(lon)
                _, _, color, tone = longitude_to_color_tone(lon)
                personality_gates[body_name] = {
                    'longitude': round(lon, 4),
                    'gate': gate,
                    'line': line,
                    'color': color,
                    'tone': tone,
                    'position_in_gate': round(pos, 4),
                    'center': GATE_CENTER.get(gate, 'unknown'),
                }
                personality_gate_set.add(gate)

            # Дизайн
            if body_name in design_positions:
                lon = design_positions[body_name]
                gate, line, pos = longitude_to_gate_line(lon)
                _, _, color, tone = longitude_to_color_tone(lon)
                design_gates[body_name] = {
                    'longitude': round(lon, 4),
                    'gate': gate,
                    'line': line,
                    'color': color,
                    'tone': tone,
                    'position_in_gate': round(pos, 4),
                    'center': GATE_CENTER.get(gate, 'unknown'),
                }
                design_gate_set.add(gate)

        result['personality_gates'] = personality_gates
        result['design_gates'] = design_gates

        # ── Центры и каналы ──
        defined_centers, complete_channels, all_activated_gates = \
            _determine_centers_and_channels(personality_gate_set, design_gate_set)

        all_centers_status = {}
        for center_id, center_name in CENTER_NAMES_RU.items():
            # Какие вороты активированы в этом центре
            center_gates_in_chart = [g for g in all_activated_gates if GATE_CENTER.get(g) == center_id]
            all_centers_status[center_id] = {
                'name': center_name,
                'defined': center_id in defined_centers,
                'activated_gates': center_gates_in_chart,
            }
        result['centers'] = all_centers_status
        result['defined_centers'] = sorted(defined_centers)
        result['undefined_centers'] = sorted(set(CENTER_NAMES_RU.keys()) - defined_centers)
        result['complete_channels'] = [
            {'gate1': ch[0], 'gate2': ch[1],
             'center1': ch[2], 'center2': ch[3],
             'name': ch[4]}
            for ch in complete_channels
        ]
        result['definition_type'] = _determine_definition(defined_centers, complete_channels)

        # ── Тип ──
        hd_type = _determine_type(defined_centers, complete_channels)
        result['type'] = hd_type

        # ── Авторитет ──
        authority = _determine_authority(defined_centers, hd_type)
        result['authority'] = authority

        # ── Профиль ──
        p_sun_line = personality_gates.get('sun', {}).get('line', 1)
        d_sun_line = design_gates.get('sun', {}).get('line', 1)
        profile = _determine_profile(p_sun_line, d_sun_line)
        result['profile'] = profile
        result['profile_description'] = PROFILE_DESCRIPTIONS_RU.get(profile, '')

        # ── Переменные (Цвет, Тон, Пищеварение, Среда, Перспектива, Мотивация) ──
        p_sun_lon = personality_positions.get('sun', 0.0)
        d_sun_lon = design_positions.get('sun', 0.0)
        variables = determine_variables(p_sun_lon, d_sun_lon)
        result['variables'] = variables

        # ── Инкарнационный Крест ──
        p_sun_gate = personality_gates.get('sun', {}).get('gate', 1)
        p_earth_gate = personality_gates.get('earth', {}).get('gate', 1)
        d_sun_gate = design_gates.get('sun', {}).get('gate', 1)
        d_earth_gate = design_gates.get('earth', {}).get('gate', 1)
        cross = _determine_incarnation_cross(
            p_sun_gate, p_earth_gate, d_sun_gate, d_earth_gate, profile)
        result['incarnation_cross'] = cross

        # ── Активированные вороты (сводка) ──
        result['all_activated_gates'] = sorted(all_activated_gates)

        # ── Неполные каналы (одни вороты из пары) ──
        hanging_gates = []
        for ch in CHANNELS:
            g1, g2 = ch[0], ch[1]
            if (g1 in all_activated_gates) != (g2 in all_activated_gates):
                active = g1 if g1 in all_activated_gates else g2
                inactive = g2 if g1 in all_activated_gates else g1
                hanging_gates.append({
                    'active_gate': active,
                    'inactive_gate': inactive,
                    'channel_name': ch[4],
                })
        result['hanging_gates'] = hanging_gates

    except Exception as e:
        logger.exception("Ошибка расчёта Бодиграфа")
        result['error'] = str(e)

    return result


# ════════════════════════════════════════════════════════════════
#  12. КОНТЕКСТ ДЛЯ ИИ — ПОДРОБНЫЙ ТЕКСТОВЫЙ ОТЧЁТ
# ════════════════════════════════════════════════════════════════

def build_hd_calculated_context(
    day: int,
    month: int,
    year: int,
    birth_time: str,
    birth_place: str = '',
) -> str:
    """Построить подробный текстовый контекст расчёта Дизайна Человека для ИИ.

    Это НЕ галлюцинация — все данные основаны на реальных астрономических расчётах.
    ИИ должен ИНТЕРПРЕТИРОВАТЬ эти данные, а не придумывать.
    """
    data = calculate_bodygraph(day, month, year, birth_time, birth_place)
    lines: List[str] = []

    if data.get('error'):
        lines.append(f"⚠️ ОШИБКА РАСЧЁТА: {data['error']}")
        lines.append("Расчёт не может быть выполнен. Попросите пользователя проверить данные.")
        return '\n'.join(lines)

    lines.append("═" * 60)
    lines.append("  РАСЧЁТ БОДИГРАФА ДИЗАЙНА ЧЕЛОВЕКА")
    lines.append("  (Реальные астрономические расчёты)")
    lines.append("═" * 60)
    lines.append(f"Движок: {data.get('engine', 'unknown')}")
    lines.append(f"Дата рождения: {data['birth_date']}")
    lines.append(f"Время рождения: {data['birth_time']}")
    lines.append(f"Место рождения: {data['birth_place'] or 'не указано'}")
    lines.append(f"UTC рождения: {data.get('birth_utc', '?')}")
    lines.append(f"Юлианский день: {data.get('birth_jd', 0):.4f}")
    lines.append(f"Дизайн-дата (UTC): {data.get('design_date_utc', '?')}")

    # ── ТИП, АВТОРИТЕТ, ПРОФИЛЬ ──
    lines.append("")
    lines.append("── ТИП ──")
    lines.append(f"  {data['type']}")

    lines.append("")
    lines.append("── АВТОРИТЕТ ──")
    lines.append(f"  {data['authority']}")

    lines.append("")
    lines.append("── ПРОФИЛЬ ──")
    lines.append(f"  {data['profile']}")
    if data.get('profile_description'):
        lines.append(f"  {data['profile_description']}")

    # ── ИНКАРНАЦИОННЫЙ КРЕСТ ──
    lines.append("")
    lines.append("── ИНКАРНАЦИОННЫЙ КРЕСТ ──")
    cross = data.get('incarnation_cross', {})
    lines.append(f"  {cross.get('full_name', '?')}")
    lines.append(f"  Ворота: {cross.get('gates', '?')}")
    lines.append(f"  Тип: {cross.get('type', '?')} ({cross.get('type_en', '?')})")

    # ── ПЕРЕМЕННЫЕ ──
    lines.append("")
    lines.append("── ПЕРЕМЕННЫЕ (4 стрелки: Цвет, Тон) ──")
    variables = data.get('variables', {})

    # Пищеварение
    dig = variables.get('digestion', {})
    lines.append(f"  ↖ Пищеварение:       {dig.get('direction', '?')} | "
                 f"Цвет {dig.get('color', '?')}: {dig.get('color_name', '?')} | "
                 f"Тон {dig.get('tone', '?')}: {dig.get('tone_name', '?')}")
    if dig.get('description'):
        lines.append(f"    {dig['description']}")

    # Среда
    env = variables.get('environment', {})
    lines.append(f"  ↗ Среда:             {env.get('direction', '?')} | "
                 f"Цвет {env.get('color', '?')}: {env.get('color_name', '?')} | "
                 f"Тон {env.get('tone', '?')}: {env.get('tone_name', '?')}")
    if env.get('description'):
        lines.append(f"    {env['description']}")

    # Перспектива
    per = variables.get('perspective', {})
    lines.append(f"  ↘ Перспектива:       {per.get('direction', '?')} | "
                 f"Тон {per.get('tone', '?')}: {per.get('tone_name', '?')} | "
                 f"Цвет {per.get('color', '?')}: {per.get('color_name', '?')}")
    if per.get('description'):
        lines.append(f"    {per['description']}")

    # Мотивация
    mot = variables.get('motivation', {})
    lines.append(f"  ↙ Мотивация:         {mot.get('direction', '?')} | "
                 f"Тон {mot.get('tone', '?')}: {mot.get('tone_name', '?')} | "
                 f"Цвет {mot.get('color', '?')}: {mot.get('color_name', '?')}")
    if mot.get('description'):
        lines.append(f"    {mot['description']}")

    # ── ОПРЕДЕЛЁННОСТЬ ──
    lines.append("")
    lines.append("── ОПРЕДЕЛЁННОСТЬ ──")
    lines.append(f"  Тип: {data.get('definition_type', '?')}")

    # ── ЦЕНТРЫ ──
    lines.append("")
    lines.append("── ЦЕНТРЫ ──")
    centers = data.get('centers', {})
    for cid in ['head', 'ajna', 'throat', 'g_center', 'heart',
                'solar_plexus', 'sacral', 'spleen', 'root']:
        c = centers.get(cid, {})
        status = '✅ ОПРЕДЕЛЁН' if c.get('defined') else '⬜ ОТКРЫТ'
        gates_list = c.get('activated_gates', [])
        gates_str = ', '.join(str(g) for g in gates_list) if gates_list else '—'
        lines.append(f"  {c.get('name', cid):40s} {status}  Ворота: {gates_str}")

    # ── КАНАЛЫ ──
    lines.append("")
    lines.append("── ПОЛНЫЕ КАНАЛЫ (определены) ──")
    complete = data.get('complete_channels', [])
    if complete:
        for ch in complete:
            c1_name = CENTER_NAMES_RU.get(ch['center1'], ch['center1'])
            c2_name = CENTER_NAMES_RU.get(ch['center2'], ch['center2'])
            lines.append(f"  {ch['name']} (Ворота {ch['gate1']}-{ch['gate2']})")
            lines.append(f"    {c1_name} ↔ {c2_name}")
    else:
        lines.append("  Нет полных каналов")

    # ── ВИСЯЧИЕ ВОРОТА ──
    lines.append("")
    lines.append("── ВИСЯЧИЕ ВОРОТА (одни из пары) ──")
    hanging = data.get('hanging_gates', [])
    if hanging:
        for hg in hanging:
            lines.append(f"  Ворота {hg['active_gate']} → {hg['inactive_gate']} "
                         f"({hg['channel_name']})")
    else:
        lines.append("  Нет висячих ворот")

    # ── ПОЗИЦИИ ЛИЧНОСТИ ──
    lines.append("")
    lines.append("── ЛИЧНОСТЬ (Осознанные / Conscious) ──")
    lines.append("  Планета              Долгота    Ворота  Линия  Цвет  Тон  Центр")
    lines.append("  " + "─" * 70)
    p_gates = data.get('personality_gates', {})
    for body in ['sun', 'earth', 'moon', 'north_node', 'south_node',
                 'mercury', 'venus', 'mars', 'jupiter', 'saturn',
                 'uranus', 'neptune', 'pluto', 'chiron']:
        if body in p_gates:
            g = p_gates[body]
            name = PLANET_NAMES_RU.get(body, body)
            center_name = CENTER_NAMES_RU.get(g.get('center', ''), g.get('center', ''))
            lines.append(f"  {name:22s} {g['longitude']:8.4f}°  "
                         f"  {g['gate']:2d}      {g['line']}     {g.get('color', '?')}     {g.get('tone', '?')}   {center_name}")

    # ── ПОЗИЦИИ ДИЗАЙНА ──
    lines.append("")
    lines.append("── ДИЗАЙН (Неосознанные / Unconscious) ──")
    lines.append("  Планета              Долгота    Ворота  Линия  Цвет  Тон  Центр")
    lines.append("  " + "─" * 70)
    d_gates = data.get('design_gates', {})
    for body in ['sun', 'earth', 'moon', 'north_node', 'south_node',
                 'mercury', 'venus', 'mars', 'jupiter', 'saturn',
                 'uranus', 'neptune', 'pluto', 'chiron']:
        if body in d_gates:
            g = d_gates[body]
            name = PLANET_NAMES_RU.get(body, body)
            center_name = CENTER_NAMES_RU.get(g.get('center', ''), g.get('center', ''))
            lines.append(f"  {name:22s} {g['longitude']:8.4f}°  "
                         f"  {g['gate']:2d}      {g['line']}     {g.get('color', '?')}     {g.get('tone', '?')}   {center_name}")

    # ── ВСЕ АКТИВИРОВАННЫЕ ВОРОТА ──
    lines.append("")
    lines.append("── ВСЕ АКТИВИРОВАННЫЕ ВОРОТА ──")
    activated = data.get('all_activated_gates', [])
    for gate_num in activated:
        center = GATE_CENTER.get(gate_num, '?')
        center_name = CENTER_NAMES_RU.get(center, center)
        keywords = GATE_KEYWORDS_RU.get(gate_num, '')
        # Откуда активировано
        sources = []
        for body, g in p_gates.items():
            if g.get('gate') == gate_num:
                sources.append(f"Личность/{PLANET_NAMES_RU.get(body, body)}")
        for body, g in d_gates.items():
            if g.get('gate') == gate_num:
                sources.append(f"Дизайн/{PLANET_NAMES_RU.get(body, body)}")
        sources_str = ', '.join(sources) if sources else '?'
        lines.append(f"  Ворота {gate_num:2d} [{center_name:30s}] {keywords}")
        lines.append(f"          Активировано: {sources_str}")

    # ── ИНСТРУКЦИЯ ДЛЯ ИИ ──
    lines.append("")
    lines.append("═" * 60)
    lines.append("  ИНСТРУКЦИЯ ДЛЯ ИИ-ИНТЕРПРЕТАТОРА")
    lines.append("═" * 60)
    lines.append("Все данные выше — РЕАЛЬНЫЕ астрономические расчёты.")
    lines.append("НЕ придумывай номера ворот, каналы или центры — всё уже рассчитано!")
    lines.append("Твоя задача: ИНТЕРПРЕТИРОВАТЬ эти данные для пользователя.")
    lines.append("")
    lines.append("При интерпретации:")
    lines.append("1. Опиши Тип и его стратегию/ауру")
    lines.append("2. Опиши Авторитет и как принимать решения")
    lines.append("3. Опиши Профиль и его роль в жизни")
    lines.append("4. Опиши Инкарнационный Крест и его значение")
    lines.append("5. Опиши определённые/открытые центры и их влияние")
    lines.append("6. Опиши полные каналы и их качества")
    lines.append("7. Упомяни висячие вороты и их потенциал")
    lines.append("8. Опиши Переменные (4 стрелки) и их значение:")
    lines.append("   - Пищеварение: как правильно питаться (Design Sun Color)")
    lines.append("   - Среда: какие условия среды оптимальны (Personality Sun Color)")
    lines.append("   - Перспектива: как видеть мир (Personality Sun Tone)")
    lines.append("   - Мотивация: что мотивирует (Design Sun Tone)")
    lines.append("9. Всё на русском языке, тёплым и понятным тоном")
    lines.append("═" * 60)

    return '\n'.join(lines)


# ════════════════════════════════════════════════════════════════
#  ТИПЫ — ОПИСАНИЯ ДЛЯ КОНТЕКСТА
# ════════════════════════════════════════════════════════════════

TYPE_DESCRIPTIONS_RU: Dict[str, str] = {
    'Генератор': (
        'Генератор — ~37% населения. Стратегия: «Откликайся». '
        'Аура: открытая, обволакивающая. Сакральный центр даёт энергию для работы. '
        'Ждёт, когда жизнь предложит что-то, и откликается сакральным звуком «угу» или «не-а». '
        'Фрустрация — ложное я. Удовлетворение — знак правильности. '
        'НЕ инициируй! Жди и откликайся.'
    ),
    'Манифестирующий Генератор': (
        'Манифестирующий Генератор — ~33% населения. Стратегия: «Откликайся, потом информируй». '
        'Аура: открытая, обволакивающая + манифестирующий импульс. '
        'Сакральный + мотор→горло = скорость и многостаночность. '
        'Быстрее всех типов, пропускает шаги, нетерпелив. '
        'Фрустрация + гнев — ложное я. Удовлетворение — знак правильности. '
        'НЕ инициируй без отклика! Но после отклика — действуй быстро.'
    ),
    'Проектор': (
        'Проектор — ~20% населения. Стратегия: «Жди приглашения». '
        'Аура: сфокусированная, проникающая. Видит других, управляет энергией. '
        'Нет сакрального — не создан для непрерывной работы. '
        'Горечь — ложное я. Успех — знак правильности. '
        'НЕ инициируй и не предлагай себя! Жди когда тебя пригласят.'
    ),
    'Манифестор': (
        'Манифестор — ~8% населения. Стратегия: «Информируй перед действием». '
        'Аура: закрытая, отталкивающая/репеллентная. Независимый инициатор. '
        'Мотор→горло без сакрального = может действовать самостоятельно. '
        'Гнев — ложное я. Мир — знак правильности. '
        'Действуй, но обязательно информируй тех, на кого повлияешь!'
    ),
    'Рефлектор': (
        'Рефлектор — ~1% населения. Стратегия: «Жди лунный цикл (28 дней)». '
        'Аура: пробная/дегустационная. Зеркало окружения. '
        'Нет определённых центров — полностью восприимчив. '
        'Разочарование — ложное я. Удивление — знак правильности. '
        'НЕ принимай решений быстро! Подожди 28 дней, обсуди с другими.'
    ),
}

AUTHORITY_DESCRIPTIONS_RU: Dict[str, str] = {
    'Эмоциональный': (
        'Эмоциональный авторитет — самый распространённый (~47%). '
        'Солярное сплетение определено. НИКОГДА не решай в моменте! '
        'Эмоциональная волна искажает ясность. Подожди — решение придёт в нейтральном состоянии. '
        'Ключ: «Спи на этом». Утро вечера мудренее — буквально.'
    ),
    'Сакральный': (
        'Сакральный авторитет — для Генераторов/МГ без определённого Солярного сплетения. '
        'Сакральный центр — центр жизненной силы — отвечает «угу/не-а» в моменте. '
        'Не думай — чувствуй ответ тела. Сакральный звук приходит мгновенно. '
        'Ключ: задай вопрос и слушай первый ответ тела.'
    ),
    'Селезёнки (Спонтанный)': (
        'Селезёночный авторитет — интуиция в моменте. '
        'Центр Селезёнки определён. Внутренний сигнал здоровья/безопасности приходит СЕЙЧАС. '
        'Не жди — действуй по интуиции. Если сомневаешься — уже поздно. '
        'Ключ: доверяй первому интуитивному импульсу.'
    ),
    'Эго (Волевой)': (
        'Эго-авторитет — Центр Сердца/Воли определён. '
        'Решение через волю: «Я сделаю это» или «Нет, не буду». '
        'Твоя истина — в твоём слове. Если сказал — сделай. '
        'Ключ: слушай свою волю — «Хочу ли я?»'
    ),
    'Самопроектируемый': (
        'Самопроектируемый авторитет — G-центр определён (без Солярного, Сакрального, Селезёнки, Эго). '
        'Истина приходит через разговор с другим. Слышь себя когда говоришь. '
        'Ключ: поговори с кем-то, кто просто слушает — и ты услышишь свою истину.'
    ),
    'Ментальный (Внешний)': (
        'Ментальный/Внешний авторитет — для Проекторов без моторных/осознаных центров. '
        'Внутреннего авторитета нет — собирай мнения, обсуждай, обдумывай. '
        'Ключ: говори с разными людьми, собирай перспективы, решай потом.'
    ),
    'Лунный': (
        'Лунный авторитет — только для Рефлекторов. '
        'Жди 28 дней (лунный цикл). Обсуждай с разными людьми. '
        'Каждый день — другой «ты». Только через цикл увидишь настоящий ответ. '
        'Ключ: терпение. Обсуждай и жди.'
    ),
}


# ════════════════════════════════════════════════════════════════
#  РАСШИРЕННЫЙ КОНТЕКСТ (с описаниями)
# ════════════════════════════════════════════════════════════════

def build_hd_full_context(
    day: int,
    month: int,
    year: int,
    birth_time: str,
    birth_place: str = '',
) -> str:
    """Полный контекст с расчётами + описания типов/авторитетов.

    Расширенная версия для глубокой консультации.
    """
    base = build_hd_calculated_context(day, month, year, birth_time, birth_place)
    data = calculate_bodygraph(day, month, year, birth_time, birth_place)

    if data.get('error'):
        return base

    extra: List[str] = []
    extra.append("")
    extra.append("═" * 60)
    extra.append("  ОПИСАНИЯ ТИПА И АВТОРИТЕТА (справка для интерпретации)")
    extra.append("═" * 60)

    # Описание типа
    hd_type = data.get('type', '')
    type_desc = TYPE_DESCRIPTIONS_RU.get(hd_type)
    if type_desc:
        extra.append(f"\n── ТИП: {hd_type} ──")
        extra.append(type_desc)

    # Описание авторитета
    authority = data.get('authority', '')
    auth_desc = AUTHORITY_DESCRIPTIONS_RU.get(authority)
    if auth_desc:
        extra.append(f"\n── АВТОРИТЕТ: {authority} ──")
        extra.append(auth_desc)

    # Описание профиля
    profile = data.get('profile', '')
    prof_desc = PROFILE_DESCRIPTIONS_RU.get(profile)
    if prof_desc:
        extra.append(f"\n── ПРОФИЛЬ: {profile} ──")
        extra.append(prof_desc)

    # Описание ворот Солнца Личности (ключевые)
    p_sun_gate = data.get('personality_gates', {}).get('sun', {}).get('gate', 0)
    if p_sun_gate and p_sun_gate in GATE_KEYWORDS_RU:
        extra.append(f"\n── СОЛНЦЕ ЛИЧНОСТИ: Ворота {p_sun_gate} ──")
        extra.append(f"  {GATE_KEYWORDS_RU[p_sun_gate]}")

    # Описание ворот Солнца Дизайна
    d_sun_gate = data.get('design_gates', {}).get('sun', {}).get('gate', 0)
    if d_sun_gate and d_sun_gate in GATE_KEYWORDS_RU:
        extra.append(f"\n── СОЛНЦЕ ДИЗАЙНА: Ворота {d_sun_gate} ──")
        extra.append(f"  {GATE_KEYWORDS_RU[d_sun_gate]}")

    return base + '\n'.join(extra)


# ════════════════════════════════════════════════════════════════
#  ТОЧКА ВХОДА — быстрый тест
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    # Пример: 15 апреля 1990, 14:30, Москва
    d, m, y = 15, 4, 1990
    t = '14:30 Москва'
    p = 'Москва'

    if len(sys.argv) >= 4:
        d, m, y = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    if len(sys.argv) >= 5:
        t = sys.argv[4]
    if len(sys.argv) >= 6:
        p = sys.argv[5]

    print(f"\nРасчёт Бодиграфа: {d:02d}.{m:02d}.{y}, {t}, {p}\n")
    context = build_hd_calculated_context(d, m, y, t, p)
    print(context)
