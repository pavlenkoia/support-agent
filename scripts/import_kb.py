from __future__ import annotations

import argparse
import hashlib
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class WikiPage:
    relative_path: str
    content: str


def slurp(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_raw_frontmatter(path: Path, source_url: str) -> None:
    text = slurp(path)
    if text.startswith("---\n") and "sha256:" in text:
        return
    body = text
    content = (
        f"---\n"
        f"source_url: {source_url}\n"
        f"ingested: {date.today().isoformat()}\n"
        f"sha256: {sha256_text(body)}\n"
        f"---\n\n"
        f"{body}"
    )
    path.write_text(content, encoding="utf-8")


def build_schema() -> str:
    return """# Wiki Schema

## Domain
Поддержка клиентов по услугам парашютных прыжков, тандемов, прогулочных полетов и подарочных сертификатов аэродрома Калачево.

## Conventions
- File names: lowercase, hyphens, no spaces.
- Every wiki page starts with YAML frontmatter.
- Use [[wikilinks]] between concept/entity pages.
- Raw sources under `raw/` are immutable copies of supplied materials.
- Support answers should rely on compiled wiki pages (`concepts/`, `entities/`, `comparisons/`, `queries/`) first; raw files are provenance only.
- Rebuild compiled pages via `python scripts/import_kb.py --profile-root <path>` instead of editing compiled pages by hand.

## Frontmatter
```yaml
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | comparison | query | summary
tags: [service, booking]
sources: [raw/documents/file.md]
confidence: high | medium | low
contested: true | false
contradictions: []
---
```

## Tag Taxonomy
- services
- certificates
- booking
- schedule
- tandem
- solo-jump
- flights
- pricing
- add-ons
- restrictions
- safety
- office
- airfield
- logistics

## Update Policy
1. Source-of-truth is `raw/documents/`.
2. Re-run the compiler after replacing or adding raw files.
3. If facts conflict, keep both with notes and mark `contested: true`.
"""


def build_pages(today: str) -> list[WikiPage]:
    pages = {
        "concepts/certificates.md": f"""---
title: Certificates
created: {today}
updated: {today}
type: concept
tags: [services, certificates, booking, office]
sources: [raw/documents/certificates-source.md, raw/documents/faq-main-source.md, raw/documents/faq-short-source.md]
confidence: high
contested: false
contradictions: []
---

# Certificates

## Summary
Подарочный сертификат можно купить онлайн на сайте `https://dzkalachevo.ru`, а также в офисе по будням или в кассе аэродрома. Сертификат не именной и действует **6 месяцев с даты покупки**.

## Purchase and delivery
- Онлайн-сертификат оформляется на сайте `https://dzkalachevo.ru`.
- В офисе купить можно по адресу [[office-chelyabinsk]] в будние дни.
- Сертификат приходит на электронную почту **в течение суток**; если письмо не пришло, сначала проверить спам, затем звонить в офис.
- Для использования сертификат нужно предъявить **в распечатанном виде** на аэродроме.

## Usage rules
- Для использования сертификата нужна предварительная запись: условия, время и место лучше согласовывать заранее через офис или соответствующий канал записи.
- Сертификат можно активировать круглогодично, но сами прыжки и полеты обычно привязаны к выходным и погоде, см. [[booking-and-schedule]].
- Если клиент хочет вернуть деньги за сертификат, агент должен направить его в офис по телефону `+7 (351) 214-30-30`.

## Related
- [[booking-and-schedule]]
- [[office-chelyabinsk]]
- [[skydiving-services]]
""",
        "concepts/booking-and-schedule.md": f"""---
title: Booking and Schedule
created: {today}
updated: {today}
type: concept
tags: [booking, schedule, tandem, solo-jump, flights]
sources: [raw/documents/general-info-source.md, raw/documents/faq-main-source.md, raw/documents/faq-short-source.md]
confidence: high
contested: false
contradictions: []
---

# Booking and Schedule

## Summary
Прыжки и полеты обычно проходят **по выходным**, а решение о проведении зависит от погоды и анонсов. Для разных услуг используются разные каналы записи.

## Booking channels
- **Самостоятельный прыжок:** запись по телефону `214-30-30`, добавочный 1; обычно в пятницу после 12:00 для субботы и в субботу после 12:00 для воскресенья.
- **Тандем-прыжок:** онлайн-форма `https://vk.cc/cMwabq` или телефон `214-30-30`, добавочный 2. После записи клиента должны добавить в чат Telegram или MAX и сообщить время.
- **Полет на Як-52:** запись напрямую у пилота по телефону `+7 (951) 440-94-77`.
- **Прогулочный полет на АН-2:** безопасно ориентировать клиента, что условия, время и место лучше согласовывать заранее, так как полеты обычно проходят по выходным; приобрести услугу можно в офисе по будням, в кассе аэродрома или на сайте `https://dzkalachevo.ru`.

## Scheduling rules
- Прыжки обычно только по субботам и воскресеньям; для групп около 20 человек возможна договоренность на другой день.
- Возможен перенос даты по погодным условиям.
- Начало полетов на Як-52 — **12:00** в рабочие дни полетов.
- Если клиент спрашивает про конкретную дату, безопасный ответ: ориентироваться на анонс в группе за несколько дней до выходных и уточнять по телефону.

## Customer guidance
- За день до поездки рекомендовать звонить на аэродром и уточнять, будут ли прыжки.
- Если клиент записан на тандем и не добавлен в чат, отправлять его звонить в офис.
- Если клиент спрашивает о 2 августа или иной отдельной дате без официального анонса, не обещать проведение — направлять следить за новостями.
- Если клиент спрашивает, лучше покупать билет/сертификат на АН-2 заранее или на месте, безопасный ответ: лучше согласовать заранее; купить можно в офисе по будням, в кассе аэродрома или на сайте `https://dzkalachevo.ru`.

## Related
- [[certificates]]
- [[skydiving-services]]
- [[flight-services]]
""",
        "concepts/skydiving-services.md": f"""---
title: Skydiving Services
created: {today}
updated: {today}
type: concept
tags: [services, tandem, solo-jump, safety, restrictions]
sources: [raw/documents/general-info-source.md, raw/documents/faq-main-source.md, raw/documents/faq-short-source.md]
confidence: high
contested: false
contradictions: []
---

# Skydiving Services

## Summary
Есть два основных сценария: **самостоятельный прыжок** и **тандем с инструктором**. Самостоятельный прыжок требует длительной подготовки в день прыжка, а тандем — короткого инструктажа.

## Solo jump
- Высота: **800–900 м** (в FAQ также формулируется как **850 м**).
- Подготовка обязательна и занимает **от 3 до 4 часов** в день прыжка.
- Первый прыжок может быть самостоятельным, если клиент проходит подготовку и подходит по ограничениям.
- Нужны паспорт, закрытая одежда, обувь с фиксацией голеностопа; берцы можно взять в прокате за 200 руб.

## Tandem jump
- Высота: **2500 м**.
- Инструктаж занимает примерно **15–30 минут**.
- Видео возможно только в тандеме; клиент может заказать съемку при предварительной записи.
- Тандем позиционируется как самый безопасный вариант первого прыжка.

## Safety and prerequisites
- Прыжки зависят от погодных условий.
- Для самостоятельных прыжков действуют погодные ограничения, см. [[restrictions-and-safety]].
- Клиентам младше 18 лет нужны дополнительные документы/присутствие родителей, см. [[restrictions-and-safety]].

## Related
- [[booking-and-schedule]]
- [[restrictions-and-safety]]
- [[pricing-and-addons]]
""",
        "concepts/flight-services.md": f"""---
title: Flight Services
created: {today}
updated: {today}
type: concept
tags: [services, flights, pricing, restrictions]
sources: [raw/documents/general-info-source.md, raw/documents/faq-main-source.md, raw/documents/faq-short-source.md]
confidence: high
contested: false
contradictions: []
---

# Flight Services

## Summary
В материалах описаны прогулочные и пилотажные полеты на **АН-2** и **Як-52**. АН-2 подходит для спокойного обзорного полета, Як-52 — для более эмоционального опыта и фигур высшего пилотажа.

## AN-2
- Используется для прогулочных полетов.
- Возможен полет **в кабине на месте второго пилота** или **в салоне** самолета.
- Подходит для более спокойного полета без фигур пилотажа.
- Возраст для места за штурвалом/полета упоминается как **с 16 лет**.
- Ограничения: вес пассажира не более **120 кг**, рост до **192 см**.
- Если клиент спрашивает про покупку полета на АН-2, безопасно отвечать: условия, время и место лучше согласовывать заранее, потому что полеты обычно проходят по выходным; приобрести услугу можно в офисе по будням, в кассе аэродрома или на сайте `https://dzkalachevo.ru`.

## Yak-52
- Двухместный спортивный самолет для полета с летчиком-инструктором.
- Возможны фигуры высшего пилотажа; также возможен вылет без пилотажа.
- Перед пилотажем рекомендуют не завтракать плотно из-за перегрузок.
- Запись — напрямую у пилота, см. [[booking-and-schedule]].

## Shared notes
- Для прогулочных полетов специальная подготовка обычно не требуется.
- Для самолетов АН-2 и Як-52 ограничение по весу пассажира — не более **120 кг**.
- Видео полета можно заказать как отдельную услугу, см. [[pricing-and-addons]].

## Related
- [[booking-and-schedule]]
- [[pricing-and-addons]]
- [[restrictions-and-safety]]
""",
        "concepts/restrictions-and-safety.md": f"""---
title: Restrictions and Safety
created: {today}
updated: {today}
type: concept
tags: [restrictions, safety, tandem, solo-jump, flights]
sources: [raw/documents/general-info-source.md, raw/documents/faq-main-source.md, raw/documents/faq-short-source.md]
confidence: high
contested: false
contradictions: []
---

# Restrictions and Safety

## Summary
Ответы клиентам об ограничениях должны быть строгими: возраст, вес, здоровье, погода и состояние клиента. Если вопрос пограничный, агент должен советовать уточнять по телефону, а не обещать допуск.

## Age and documents
- Самостоятельные прыжки: **с 14 лет**, до 18 лет — разрешение родителей и обычно присутствие одного из родителей.
- Тандем: в материалах встречается формулировка **с 12 лет**, но с уточнением ограничений по телефону.
- Для посещения/прыжка нужен **паспорт**; водительское удостоверение или военный билет не подходят как замена в указанных материалах.

## Weight and health
- Самостоятельный прыжок: вес **45–90 кг**.
- Тандем: вес **до 85 кг**; при превышении направлять в офис для уточнения.
- Полеты на самолетах: вес пассажира **до 120 кг**.
- Ограничения по здоровью: астма, нарушения слуха, некоторые сердечно-сосудистые заболевания, высокое давление, свежие переломы и состояние опьянения.

## Weather and equipment
- Для самостоятельных прыжков: ветер более **5 м/с**, сильные осадки и низкая облачность ниже высоты выброски делают прыжок невозможным.
- Зимой прыжки возможны; нужна более теплая одежда.
- Прыжки с собственной камерой **не допускаются**.
- Экипировка вроде очков, шлема, комбинезона и перчаток **не выдается** по короткому FAQ.

## Related
- [[skydiving-services]]
- [[flight-services]]
- [[office-chelyabinsk]]
""",
        "concepts/pricing-and-addons.md": f"""---
title: Pricing and Add-ons
created: {today}
updated: {today}
type: concept
tags: [pricing, add-ons, certificates, flights, tandem]
sources: [raw/documents/general-info-source.md, raw/documents/faq-main-source.md, raw/documents/faq-short-source.md]
confidence: medium
contested: false
contradictions: []
---

# Pricing and Add-ons

## Summary
Часть цен в источниках указана явно, но общая витрина цен вынесена на отдельную ссылку. Поэтому агент может уверенно отвечать только там, где в wiki зафиксирована точная сумма.

## Explicit prices in sources
- Скидка для школьников и студентов очного отделения: **7000 руб.**
- Видеосъемка тандем-прыжка парашютистом-оператором: **4000 руб.**
- Видеосъемка тандем-инструктором на GoPro: **2500 руб.**
- Видеосъемка полета: **2500 руб.**
- Прокат берцев: **200 руб.**

## Pricing policy for support replies
- Если клиент просит общую цену на услугу, безопасно ссылаться на официальный прайс: `https://vk.cc/cYzS5j`.
- Фото/видео онлайн вместе с услугой купить нельзя: по источникам их оформляют только в офисе или кассе аэродрома.
- Подготовка к прыжку входит в стоимость прыжка.
- По безналичному расчету можно платить в офисе, в кассе аэродрома или купить сертификат онлайн.

## Related
- [[certificates]]
- [[skydiving-services]]
- [[flight-services]]
""",
        "entities/office-chelyabinsk.md": f"""---
title: Office in Chelyabinsk
created: {today}
updated: {today}
type: entity
tags: [office, booking, certificates]
sources: [raw/documents/certificates-source.md, raw/documents/faq-main-source.md]
confidence: high
contested: false
contradictions: []
---

# Office in Chelyabinsk

## Summary
Офис используется для покупки сертификатов, очной оплаты и решения нестандартных вопросов по возвратам и уточнениям.

## Contact details
- Адрес: **г. Челябинск, ул. 8 Марта, 108, офис 411**.
- Режим работы: **пн–пт, 09:00–17:00**.
- Перед визитом рекомендовано звонить по телефону **+7 (351) 214-30-30**.

## Use cases
- Покупка подарочного сертификата офлайн.
- Уточнение возврата денег за сертификат.
- Уточнение пограничных ограничений по весу или допуску.

## Related
- [[certificates]]
- [[booking-and-schedule]]
- [[restrictions-and-safety]]
""",
        "entities/kalachevo-airfield.md": f"""---
title: Kalachevo Airfield
created: {today}
updated: {today}
type: entity
tags: [airfield, logistics, flights, solo-jump, tandem]
sources: [raw/documents/faq-main-source.md, raw/documents/general-info-source.md]
confidence: high
contested: false
contradictions: []
---

# Kalachevo Airfield

## Summary
На аэродроме Калачево проходят прыжки и полеты. Клиентов часто нужно ориентировать не только по услуге, но и по месту, времени прибытия и способу добраться.

## Location
- Прыжки и полеты проходят на аэродроме **Калачево** круглый год.
- Ориентиры и маршрут подробно описаны в FAQ: Троицкий тракт, развязка на Еткуль, затем въезд на аэродром через 5 км.
- Общественный транспорт: автобусы **140** и **187** от областной больницы, маршрутка **302** от ЖД вокзала.

## Operational notes
- На аэродроме клиент предъявляет распечатанный сертификат, если использует сертификат.
- Очная оплата некоторых дополнительных услуг происходит на территории аэродрома наличными.
- Время приезда на тандем клиенту обычно сообщают через Telegram/MAX чат после записи.

## Related
- [[booking-and-schedule]]
- [[certificates]]
- [[skydiving-services]]
""",
    }
    return [WikiPage(relative_path=path, content=content) for path, content in pages.items()]


def build_index(today: str) -> str:
    return f"""# Wiki Index

> Content catalog for the parachute support LLM Wiki.
> Last updated: {today} | Total pages: 8

## Entities
- [[kalachevo-airfield]] — где проходят прыжки и полеты, как добраться, что важно на месте.
- [[office-chelyabinsk]] — офис в Челябинске для сертификатов, оплаты и нестандартных вопросов.

## Concepts
- [[booking-and-schedule]] — каналы записи, зависимость от погоды, выходные и анонсы.
- [[certificates]] — покупка, срок действия, доставка и использование подарочных сертификатов.
- [[flight-services]] — прогулочные и пилотажные полеты на АН-2 и Як-52.
- [[pricing-and-addons]] — что можно уверенно сказать по ценам и допуслугам.
- [[restrictions-and-safety]] — возраст, вес, здоровье, погода, камеры и экипировка.
- [[skydiving-services]] — самостоятельный прыжок и тандем: высота, подготовка, особенности.

## Comparisons

## Queries
"""


def build_log(today: str) -> str:
    return f"""# Wiki Log

> Chronological record of LLM Wiki actions.

## [{today}] compile | Parachute wiki rebuilt from raw sources
- Refreshed `SCHEMA.md`, `index.md`, compiled concept/entity pages.
- Source-of-truth remains `raw/documents/`.
- Legacy staging directories were intentionally removed after raw ingest.
"""


def compile_profile(profile_root: Path) -> None:
    kb_root = profile_root / "kb"
    raw_dir = kb_root / "raw" / "documents"
    if not raw_dir.exists():
        raise SystemExit(f"raw documents directory not found: {raw_dir}")

    required_raw = {
        "certificates-source.md": "local:certificates-source.md",
        "general-info-source.md": "local:general-info-source.md",
        "faq-main-source.md": "local:faq-main-source.md",
        "faq-short-source.md": "local:faq-short-source.md",
    }
    for filename, source_url in required_raw.items():
        ensure_raw_frontmatter(raw_dir / filename, source_url)

    today = date.today().isoformat()
    for directory in ["concepts", "entities", "comparisons", "queries"]:
        (kb_root / directory).mkdir(parents=True, exist_ok=True)

    (kb_root / "SCHEMA.md").write_text(build_schema(), encoding="utf-8")
    (kb_root / "index.md").write_text(build_index(today), encoding="utf-8")
    (kb_root / "log.md").write_text(build_log(today), encoding="utf-8")

    for page in build_pages(today):
        target = kb_root / page.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page.content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile parachute KB raw sources into LLM Wiki pages.")
    parser.add_argument(
        "--profile-root",
        default=os.environ.get("SUPPORT_AGENT_PROFILE_ROOT") or os.environ.get("KNOWLEDGE_ROOT"),
        help="Profile root directory that contains kb/ (example: /home/tian/support-agent-profiles/parachute)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.profile_root:
        raise SystemExit("profile root is required via --profile-root or SUPPORT_AGENT_PROFILE_ROOT")
    profile_root = Path(args.profile_root)
    compile_profile(profile_root)
    print(f"compiled wiki at {profile_root / 'kb'}")


if __name__ == "__main__":
    main()
