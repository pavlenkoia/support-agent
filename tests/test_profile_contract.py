from __future__ import annotations

from pathlib import Path

import pytest

from app.services.knowledge_bundle import validate_compiled_bundle
from app.services.policy import PolicyService
from scripts.build_runtime_profile import (
    ProfileSourceError,
    build_runtime_profile,
    validate_profile_source,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "deploy" / "profile-source"


def test_canonical_profile_source_has_no_prompt_facts_or_wiki_scenarios() -> None:
    validate_profile_source(SOURCE)


def test_canonical_profile_requires_factual_tools_for_substantive_customer_replies() -> None:
    prompt = SOURCE.joinpath("SYSTEM_PROMPT.md").read_text(encoding="utf-8")
    assert "Перед ответом, который содержит или требует факт" in prompt
    assert "По умолчанию сначала проверь Wiki" in prompt
    assert "Контекстное ограничение обязательно" in prompt


def test_profile_declares_no_answer_contact_evidence_without_customer_template() -> None:
    evidence = PolicyService(profile_root=str(SOURCE)).no_answer_policy_evidence()
    assert evidence == [{
        "source_ref": "kb/entities/office-chelyabinsk.md",
        "text": "Для уточнения вопроса можно позвонить в офис в рабочее время.",
    }]


def test_prompts_contain_no_profile_business_literals() -> None:
    text = "\n".join(
        SOURCE.joinpath(name).read_text(encoding="utf-8").casefold()
        for name in ("SYSTEM_PROMPT.md", "KB_AGENT_PROMPT.md")
    )
    forbidden = (
        "калачево",
        "аэродром",
        "прыж",
        "тандем",
        "сертификат",
        "самолёт",
        "ан-2",
        "як-52",
        "скид",
        "цена",
        "прайс",
        "214-30-30",
        "vk.cc",
        "dzkalachevo",
    )
    assert [marker for marker in forbidden if marker in text] == []


def test_removed_prompt_facts_are_preserved_in_canonical_wiki() -> None:
    expected_by_page = {
        "kb/entities/kalachevo-airfield.md": [
            "доступны только на аэродроме Калачево в Челябинске",
            "проходят на аэродроме круглый год",
            "автобусы 140 и 187",
            "оплачиваются наличными в кассе аэродрома",
        ],
        "kb/entities/office-chelyabinsk.md": [
            "ул. 8 Марта, 108, офис 411",
            "понедельник–пятница, 09:00–17:00",
            "+7 (351) 214-30-30",
            "звонок повторяют в рабочее время",
            "Вопросы продления и возврата сертификата",
            "потеря скачанных фото- или видеоматериалов",
            "Условия фотосессии или фотосъёмки",
        ],
        "kb/concepts/pricing-and-addons.md": [
            "https://vk.cc/cYzS5j",
            "Точная текущая стоимость в Wiki не фиксируется",
            "Фото- и видеосъёмка не оформляется онлайн",
            "Видеосъёмка прыжка доступна только для тандема",
            "Примеры видео с прыжков можно посмотреть в ленте группы ВКонтакте",
            "оплачивается наличными на территории аэродрома",
            "своевременно продлённого студенческого билета",
            "Для остальных категорий скидки не предусмотрены",
            "Скидки на день рождения, юбилей и праздники не предусмотрены",
        ],
        "kb/concepts/certificates.md": [
            "Сертификат не именной",
            "действует 6 месяцев с даты покупки",
            "https://dzkalachevo.ru",
            "электронный сертификат можно распечатать самостоятельно",
            "Доставка и курьерская отправка не предусмотрены",
            "приходит на электронную почту в течение суток",
            "Официальный email для письменных обращений по сертификатам не указан",
            "сертификат предъявляют в распечатанном виде",
            "Для использования необходима предварительная запись",
            "Сертификат можно активировать круглогодично",
            "Вопросы продления и возврата решает офис",
        ],
        "kb/concepts/booking-and-schedule.md": [
            "Условия записи на конкретную дату публикуются в закреплённом посте или анонсе",
        ],
        "kb/concepts/booking-channels.md": [
            "добавочный 1",
            "пятницу после 12:00 на субботу",
            "https://vk.cc/cMwabq",
            "добавочный 2",
            "чат Telegram или MAX",
            "Текущий набор группы и наличие мест",
            "+7 (951) 440-94-77",
        ],
        "kb/concepts/ordinary-jump-schedule.md": [
            "Обычные прыжки обычно проходят по субботам и воскресеньям",
            "Для группы около 20 человек",
            "Сам по себе день недели не подтверждает и не исключает проведение прыжков",
            "Проведение и перенос зависят от погодных условий",
        ],
        "kb/concepts/skydiving-services.md": [
            "Первый прыжок можно выполнить самостоятельно или в тандеме",
            "800–900 м",
            "3–4 часа",
            "парашют Д-6",
            "паспорт, закрытая одежда и обувь с фиксацией голеностопа",
            "Высота — 2500 м",
            "15–30 минут",
            "наиболее безопасный формат первого прыжка",
        ],
        "kb/concepts/flight-services.md": [
            "в кабине на месте второго пилота или в салоне",
            "без фигур высшего пилотажа",
            "Полёты с детьми проводятся только на АН-2",
            "с 16 лет",
            "рост до 192 см",
            "Прогулочные полёты обычно проходят по выходным",
            "Як-52 — двухместный спортивный самолёт",
            "ориентир начала вылетов в день полётов — 12:00",
            "специальная подготовка пассажира обычно не требуется",
            "максимальный вес пассажира — 120 кг",
        ],
        "kb/concepts/restrictions-and-safety.md": [
            "Самостоятельный прыжок доступен с 14 лет",
            "Для тандем-прыжка возрастное условие у 15-летнего клиента выполнено",
            "До 18 лет требуется разрешение родителей",
            "возраст с 12 лет",
            "другой — с 14 лет",
            "точный возрастной допуск к тандему уточняют по телефону",
            "Для прыжка нужен паспорт",
            "Самостоятельный прыжок: вес 45–90 кг",
            "Тандем: вес до 85 кг",
            "Пограничные случаи и превышение ограничения уточняют в офисе",
            "Полёты на самолётах: вес пассажира до 120 кг",
            "астме, нарушениях слуха",
            "ветер более 5 м/с",
            "Зимой прыжки возможны",
            "Прыжки с собственной камерой не допускаются",
        ],
        "kb/concepts/payment-methods.md": [
            "Покупку услуги можно оплатить безналично в кассе аэродрома, в офисе и на сайте",
            "Подарочный сертификат можно купить на сайте",
        ],
        "kb/concepts/equipment-and-rental.md": [
            "Отдельного опубликованного прайс-листа на обмундирование нет",
            "Очки, шлем, комбинезон и перчатки не выдаются",
            "Отсутствие этих предметов само по себе не препятствует прыжку",
            "Берцы можно взять в прокате на месте",
            "прокатные берцы не обязательны",
            "Опубликованной цены проката берцев нет",
        ],
    }
    missing: list[str] = []
    for relative_path, facts in expected_by_page.items():
        text = SOURCE.joinpath(relative_path).read_text(encoding="utf-8")
        for fact in facts:
            if fact not in text:
                missing.append(f"{relative_path}: {fact}")

    assert missing == []


def test_profile_source_rejects_business_url_in_system_prompt(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "kb" / "concepts").mkdir(parents=True)
    (source / "SYSTEM_PROMPT.md").write_text("Use https://business.example\n", encoding="utf-8")
    (source / "KB_AGENT_PROMPT.md").write_text("Read facts only.\n", encoding="utf-8")

    with pytest.raises(ProfileSourceError, match=r"SYSTEM_PROMPT\.md contains a URL"):
        validate_profile_source(source)


def test_profile_source_rejects_scenario_instruction_in_wiki(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "kb" / "concepts").mkdir(parents=True)
    (source / "SYSTEM_PROMPT.md").write_text("Generic contract.\n", encoding="utf-8")
    (source / "KB_AGENT_PROMPT.md").write_text("Generic reader.\n", encoding="utf-8")
    (source / "kb" / "concepts" / "facts.md").write_text(
        "# Facts\n\nЕсли клиент спросил, ответь готовой фразой.\n", encoding="utf-8"
    )

    with pytest.raises(ProfileSourceError, match="scenario-style instruction"):
        validate_profile_source(source)


def test_build_runtime_profile_produces_valid_bundle(tmp_path: Path) -> None:
    target = tmp_path / "runtime-profile"
    build_runtime_profile(SOURCE, target)

    assert target.joinpath("SYSTEM_PROMPT.md").read_bytes() == SOURCE.joinpath("SYSTEM_PROMPT.md").read_bytes()
    assert target.joinpath("KB_AGENT_PROMPT.md").read_bytes() == SOURCE.joinpath("KB_AGENT_PROMPT.md").read_bytes()
    assert validate_compiled_bundle(target / "kb")["valid"] is True


def test_application_runtime_contains_no_profile_business_literals() -> None:
    app_root = ROOT / "app"
    forbidden = (
        "214-30-30",
        "vk.cc",
        "dzkalachevo",
        "Калачево",
        "калачево",
        "тандем",
        "сертификат",
        "аэродром",
    )
    violations: list[str] = []
    for path in sorted(app_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                violations.append(f"{path.relative_to(app_root)}: {marker}")

    assert violations == []
