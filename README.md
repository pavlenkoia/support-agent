# Агент поддержки

Сервис поддержки на FastAPI с PostgreSQL, Telegram/VK-воркерами, read-only Viewer и LLM-ответами. В production агент получает **один структурированный JSON-артефакт знаний**; Wiki и Markdown не передаются провайдеру.

## Актуальная архитектура знаний

```text
Авторская KB / support-governor
        ↓
курируемый реестр facts
        ↓ валидация и candidate-сборка
knowledge-base.v1.json
        ↓ атомарная публикация
/data/profile/kb/knowledge-base.v1.json
        ↓
один provider call агента
```

### Runtime KB

Единственный model-facing файл:

```text
/data/profile/kb/knowledge-base.v1.json
```

Его схема:

```json
{
  "schema_version": 1,
  "facts": [
    {
      "id": "jump.schedule.weekends",
      "text": "Прыжки обычно проходят по субботам и воскресеньям.",
      "conditions": ["Проведение зависит от погоды."],
      "source_refs": ["schedule:01"]
    }
  ]
}
```

В KB допустимы только явные курируемые факты: семантический `id`, клиентский текст, условия применимости и логические ссылки на источник. В runtime запрещены Markdown, YAML/frontmatter, пути файлов, raw-документы, журналы, редакторские инструкции, навигация и автоматически извлечённые из Wiki фразы.

### Единственный authoring source

Каноническими являются явные `runtime_facts` в YAML-frontmatter тематических Wiki-страниц под `deploy/profile-source/kb/`. Компилятор читает только эти blocks и собирает из всех страниц один `kb/knowledge-base.v1.json`; отдельного редактируемого реестра facts нет.

`scripts/build_runtime_profile.py` не выводит факты из Markdown regex-разбором.

## Работа support-governor

Governor работает через ограниченный tool `support_kb_bundle` и не получает доступа к коду приложения, Docker, Git, промптам или секретам.

Поддерживаемый безопасный цикл изменения facts:

1. `read` — Governor читает актуальную тематическую Wiki-страницу вместе с её `runtime_facts` и SHA-256.
2. Governor меняет обычный текст и соответствующие `runtime_facts` в одной полной странице.
3. `apply` передаёт страницу, `reason`, `expected_sha256`; сначала допускается `dry_run: true`.
4. Publisher валидирует Wiki, компилирует facts из её же snapshot и атомарно заменяет runtime KB.
5. Следующий запрос воркера читает новый JSON без перезапуска контейнеров.

`expected_sha256` защищает от перезаписи более свежей правки. При ошибке валидации или сборки текущий production KB остаётся прежним. Receipt и backup создаются publisher-ом; rollback возможен заменой KB из его backup.

Обычная авторская Wiki — единственный источник facts и provenance-слой: никакого отдельного редактируемого JSON-реестра нет.

## Компоненты

- `app/` — FastAPI и логика агента;
- `viewer-web/` — React/Tailwind Viewer VK-диалогов;
- `deploy/` — профиль, deploy-материалы и runtime-монтирование;
- `scripts/` — сборка профиля и эксплуатационные скрипты;
- `tests/` — тесты контрактов и runtime-поведения.

## Локальные проверки

```bash
uv run ruff check .
uv run pytest -q
python scripts/build_runtime_profile.py --source deploy/profile-source --target /tmp/support-profile
python scripts/publish_kb.py --dry-run
```

## Health check

```bash
curl http://127.0.0.1:8000/health
```

## Production release

Перед выпуском профиль собирают из canonical source, проверяют candidate и точное соответствие внешнему runtime-профилю. Затем выполняют:

```bash
python3 scripts/release.py
```

Не выкатывайте отдельные Compose-сервисы вместо проверенной процедуры релиза. Runtime env и секреты находятся вне репозитория.
