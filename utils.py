import pymorphy2


def pluralize(word, count):
    morph = pymorphy2.MorphAnalyzer()
    # Разбор слова
    parsed = morph.parse(word)[0]
    # Склонение по числу
    correct_form = parsed.make_agree_with_number(count).word
    return f"{count} {correct_form}"

# Пример использования
# print(pluralize("день", 1))   # 1 день