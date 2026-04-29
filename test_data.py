DOMAINS = [
    "introversion",
    "need_support",
    "directness",
    "detail_preference",
    "anxiety",
    "self_esteem",
    "emotional_sensitivity",
    "trust",
    "rumination",
    "control_need",
]


TEST = [
    # --- introversion ---
    {"question": "После общения мне нужно время побыть одному.", "key": "introversion", "reverse": False},
    {"question": "Я быстро устаю от большого количества людей.", "key": "introversion", "reverse": False},

    # --- need_support ---
    {"question": "В трудный момент мне важно, чтобы меня поддержали.", "key": "need_support", "reverse": False},
    {"question": "Я предпочитаю справляться с проблемами полностью самостоятельно.", "key": "need_support", "reverse": True},

    # --- directness ---
    {"question": "Мне комфортнее, когда со мной говорят прямо.", "key": "directness", "reverse": False},
    {"question": "Я предпочитаю мягкие и обходные формулировки.", "key": "directness", "reverse": True},

    # --- detail_preference ---
    {"question": "Мне легче понимать, когда объясняют подробно.", "key": "detail_preference", "reverse": False},
    {"question": "Мне обычно достаточно короткого ответа.", "key": "detail_preference", "reverse": True},

    # --- anxiety ---
    {"question": "Я часто испытываю тревогу или внутреннее напряжение.", "key": "anxiety", "reverse": False},
    {"question": "Я часто ожидаю, что что-то пойдёт не так.", "key": "anxiety", "reverse": False},
    {"question": "Я обычно спокоен и расслаблен.", "key": "anxiety", "reverse": True},

    # --- self_esteem ---
    {"question": "Я часто сомневаюсь в себе.", "key": "self_esteem", "reverse": False},
    {"question": "Я в целом доволен собой.", "key": "self_esteem", "reverse": True},

    # --- emotional_sensitivity ---
    {"question": "Я сильно реагирую на критику.", "key": "emotional_sensitivity", "reverse": False},
    {"question": "Меня трудно задеть словами.", "key": "emotional_sensitivity", "reverse": True},

    # --- trust ---
    {"question": "Мне легко доверять людям.", "key": "trust", "reverse": False},
    {"question": "Я часто ожидаю подвоха от людей.", "key": "trust", "reverse": True},

    # --- rumination ---
    {"question": "Я часто прокручиваю мысли снова и снова.", "key": "rumination", "reverse": False},
    {"question": "Мне трудно отпустить ситуацию.", "key": "rumination", "reverse": False},

    # --- control_need ---
    {"question": "Мне важно держать ситуацию под контролем.", "key": "control_need", "reverse": False},
    {"question": "Мне сложно, когда всё идёт не по плану.", "key": "control_need", "reverse": False},
    {"question": "Я спокойно отношусь к неопределённости.", "key": "control_need", "reverse": True},
]