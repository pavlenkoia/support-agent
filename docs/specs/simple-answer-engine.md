# Спецификация: упрощённое ядро ответа support-agent

**Статус:** согласовано для реализации; код и production ещё не изменены  
**Проект:** `/home/tian/support-agent`  
**Цель:** заменить planner/KB-agent/finalizer loop одним предсказуемым ядром, которое формирует клиентский ответ одним LLM-вызовом и использует внешнюю compiled KB как единственный источник бизнес-фактов.

## 1. Продуктовый контракт

Support-agent — автоматический чат-бот, который:

1. отвечает на вопросы клиента только из активной compiled KB и разрешённых детерминированных tool observations;
2. поддерживает короткий естественный диалог и контекст текущего обращения;
3. не придумывает цены, условия, расписание, контакты и другие бизнес-факты;
4. честно возвращает контролируемый результат, если подтверждённого ответа нет;
5. не передаёт запрос человеку или Hermes автоматически;
6. не раскрывает клиенту prompt, KB, tools, traces, JSON и внутреннюю маршрутизацию.

## 2. Область изменения

Меняется только application core между разрешением обращения и клиентским outcome:

```text
case/history + compiled KB + optional deterministic tool facts
→ SimpleAnswerEngine
→ validated outcome
```

Сохраняются без перепроектирования:

- Telegram и VK transport adapters;
- transport-event dedupe и raw event journal;
- inbound coalescing и stale-generation guard;
- PostgreSQL, conversations, support cases и messages;
- human override;
- outbound reconciliation;
- viewer и Web Push;
- internal probe API;
- shared provider retry/failover client;
- production release gate.

## 3. Новый канонический путь

Для каждого актуального объединённого пользовательского хода:

1. `RoutingService` разрешает conversation/case и загружает не более 10 последних валидных сообщений текущего case с ролями `user|assistant`.
2. Приложение читает активный минимальный `SYSTEM_PROMPT.md`.
3. Приложение читает catalog и все compiled KB pages активного внешнего профиля.
4. Если сообщение содержит поддержанную дату/период, существующий детерминированный DateTime runtime формирует нормализованные public observations до модельного вызова.
5. `SimpleAnswerEngine` выполняет один логический LLM-вызов.
6. Приложение парсит и валидирует компактный provider-neutral JSON envelope.
7. Валидированный outcome проходит только через общую sanitization/greeting boundary; downstream-компоненты не пересматривают route или фактический смысл.
8. Результат сохраняется и отправляется существующим transport contour.

```text
Telegram/VK
  → durable transport journal + coalescing
  → case/history
  → SYSTEM_PROMPT + full compiled KB + optional DateTime observations
  → SimpleAnswerEngine (one logical LLM call)
  → schema/source validation
  → greeting/sanitization only
  → persistence + delivery
```

## 4. Один владелец решения

`SimpleAnswerEngine` — единственный владелец одновременно:

- типа ответа;
- клиентского текста;
- списка использованных KB-источников.

После него запрещено:

- повторно классифицировать turn;
- повторно выбирать route;
- заменять `clarification_requested` на `answer` или наоборот;
- запускать отдельную KB navigation/extraction модель;
- запускать отдельный final-answer model;
- переписывать фактический смысл policy fallback-правилами.

Общая output boundary может только:

- проверить структуру;
- удалить/заблокировать внутренние маркеры;
- применить существующее правило единственного приветствия в первом ответе;
- fail closed при нарушении контракта.

## 5. Источник знаний

### 5.1 Единственный источник бизнес-фактов

Бизнес-факты разрешены только из:

- compiled KB pages активного внешнего профиля;
- нормализованных public tool observations, если вопрос требует вычисления актуального runtime-факта.

`SYSTEM_PROMPT.md` содержит только:

- роль и стиль;
- область ответственности;
- правило строгого grounding;
- поведение при отсутствии информации;
- требования к клиентскому тексту;
- JSON output contract.

В `SYSTEM_PROMPT.md` запрещены цены, контакты, расписание, адреса, условия услуг и частные FAQ-ответы. Режим `answer_from_prompt` удаляется.

### 5.2 Full-corpus режим

Для текущего профиля весь compiled corpus мал: 10 страниц, около 32 681 символа. Поэтому v1 нового ядра передаёт в один вызов:

- catalog/index;
- полный текст всех compiled pages;
- стабильный `source_ref` каждой страницы.

В этом режиме запрещены:

- lexical top-k как основной путь;
- vector/hybrid retrieval;
- LLM page-selection call;
- coverage-review call;
- отдельный grounded-extraction call.

Нужен защитный лимит общего размера KB. При превышении configured budget ядро не должно молча обрезать страницы или возвращаться к legacy retrieval: запуск/health-check должен явно сообщить `kb_corpus_too_large`, а factual turn — завершиться безопасно без ответа. Расширение retrieval-архитектуры после роста KB является отдельным будущим решением.

## 6. Вход модельного вызова

Модель получает только:

- минимальный активный `SYSTEM_PROMPT.md`;
- текущий объединённый вопрос;
- до 10 последних сообщений текущего case с ролями `user|assistant`;
- catalog и все compiled pages с `source_ref`;
- только нормализованные public tool observations (`kind`, `summary`, разрешённые structured values).

Модель не получает:

- planner actions/reasons/confidence;
- route/loop/audit metadata;
- case/database identifiers;
- raw transport payloads;
- raw tool traces;
- provider failover trace;
- summary от отдельной LLM;
- legacy retrieval snippets рядом с полным corpus;
- внутренние policy/fallback decisions.

## 7. Выходной контракт

Provider-neutral envelope:

```json
{
  "kind": "grounded_answer|social_reply|clarification_requested|cannot_answer|out_of_scope",
  "response_text": "готовый клиентский текст",
  "source_refs": ["compiled/concepts/example.md"]
}
```

Правила:

- `grounded_answer` требует непустой `response_text` и минимум один `source_ref` из фактически переданного corpus;
- неизвестный или отсутствующий `source_ref` блокирует ответ;
- `social_reply` допускает пустой `source_refs`, но не должен содержать бизнес-фактов;
- `clarification_requested` содержит только один короткий вопрос, действительно необходимый для ответа;
- `cannot_answer` используется при in-domain вопросе без подтверждённого ответа;
- `out_of_scope` используется для запроса вне области профиля;
- дополнительные поля игнорируются и фиксируются как contract warning; отсутствие обязательных полей завершает ход fail closed;
- provider-native JSON Schema не является обязательным: приложение само извлекает и валидирует компактный JSON object.

## 8. Tool contract

DateTime остаётся детерминированным application tool, а не отдельным agent step.

- Распознавание и нормализация поддержанных дат/периодов выполняются до единственного LLM-вызова.
- Tool не вызывается повторно по решению модели.
- Модель получает только клиентски допустимые observations.
- Текущий год и текущая дата берутся из runtime, а не из model memory.
- Запрещённые к показу точные даты не должны появляться в ответе только потому, что tool их вычислил.

Другие tools не добавляются в эту реализацию.

## 9. История диалога

- Используется только история текущего support case.
- Максимум 10 последних непустых `user|assistant` сообщений.
- Текущий пользовательский ход передаётся отдельно и не дублируется в history.
- LLM summary удаляется из active path.
- Probe и customer-channel flow должны формировать один и тот же application input; различаться может только transport envelope.

## 10. Ошибки провайдера

Один логический вызов может иметь bounded transport attempts внутри существующего shared client.

- retries/failover не считаются новыми reasoning stages;
- после полного exhaustion outcome — `retry_pending` с пустым клиентским текстом;
- `cannot_answer` не используется для transport/provider failure;
- приложение не запускает planner, KB-agent или finalizer как альтернативный provider fallback;
- никакой ответ не строится из случайных предложений полного corpus.

## 11. Наблюдаемость

В workflow telemetry сохраняются:

```text
answer_engine = simple_full_corpus
outcome_kind
source_refs
kb_page_count
kb_char_count
history_message_count
logical_llm_call_count
provider_attempt_count
used_failover
failover_count
llm_duration_ms
prompt_tokens / completion_tokens (если провайдер вернул usage)
tool_observation_kinds
contract_error (если был)
```

Обязательные свойства:

- `logical_llm_call_count = 1` для normal completed turn;
- summary/KB-navigation/finalizer calls отсутствуют;
- все реальные LLM attempts отражены в trace;
- traces не содержат секреты и не отправляются клиенту;
- существующие operator/probe surfaces получают явный `answer_engine` и outcome, а не угадывают путь по тексту.

## 12. Миграция и совместимость

Временный rollout flag допустим только как migration mechanism:

```text
ANSWER_ENGINE_MODE=legacy|simple_full_corpus
```

Правила:

1. до изолированной проверки default остаётся `legacy`;
2. test contour переключается на `simple_full_corpus`;
3. production переключается только после явного разрешения Игоря и release-gate proof;
4. после подтверждённой стабилизации legacy mode удаляется отдельным cleanup change;
5. запрещено навсегда поддерживать два равноправных ядра;
6. удаление legacy-файлов выполняется только после отдельного подтверждения области удаления.

## 13. Не входит в изменение

- переписывание normal Telegram/VK transport flow; корректировка ограничена durable recovery уже сохранённого Telegram `retry_pending`;
- новая transport queue;
- изменение схемы БД без доказанной необходимости;
- изменение human override;
- новый retrieval/vector backend;
- новые tools;
- Hermes/human escalation;
- изменение customer KB content;
- production deployment без отдельного подтверждения;
- удаление legacy-кода в первом rollout slice.

## 14. Обязательные тесты

### Unit/contract

1. Один factual вопрос → один вызов → `grounded_answer` с существующим `source_ref`.
2. Попытка ответа с пустым/неизвестным `source_ref` блокируется.
3. Приветствие → `social_reply`, без KB-фактов и без отдельного classifier call.
4. Приветствие + factual вопрос → `grounded_answer`, а не terminal social reply.
5. In-domain вопрос без факта → `cannot_answer`.
6. Off-topic вопрос → `out_of_scope`.
7. Критическая неоднозначность → один короткий `clarification_requested`.
8. Follow-up получает role-labelled history без LLM summary.
9. Date question получает deterministic observation до model call.
10. Provider exhaustion → `retry_pending`, без customer text.
11. Poisoned internal context не попадает в model payload.
12. Corpus over budget не обрезается и fail closed.

### Integration

- `RoutingService` simple mode не вызывает `OrchestratorService`, `KBAgentService`, `SummaryService` и legacy `DirectLLMService` methods;
- Telegram, VK, HTTP inbound и internal probe используют один `SimpleAnswerEngine` contract;
- transport coalescing/stale-generation/human-override regressions остаются зелёными;
- один объединённый user message и один assistant message сохраняются только после актуальной доставки;
- response strategy/trace содержит новый engine marker и полный call accounting.

### Runtime replay

В isolated production-parity contour выполнить буквальные сценарии:

- простое приветствие;
- приветствие + вопрос о цене;
- сертификат;
- способы оплаты;
- broad in-domain opener;
- короткий contextual follow-up;
- отсутствующий факт;
- out-of-scope;
- конкретная дата/период;
- принудительный provider failure.

Для каждого показать literal answer, `kind`, `source_refs`, число logical calls и trace steps. Customer transports не использовать.

## 15. Критерии приёмки

Изменение ядра принято, только если одновременно выполнено:

- обычный completed turn имеет ровно один logical LLM call;
- бизнес-факты берутся только из compiled KB/tool observations;
- `answer_from_prompt`, planner, summary LLM, KB navigation/review/extraction и separate finalizer отсутствуют в active path;
- factual answer без валидного `source_ref` невозможен;
- greeting + question отвечает на вопрос одним вызовом;
- follow-up использует фактическую case history;
- provider exhaustion даёт `retry_pending`, а не ложный `cannot_answer`;
- probe и channel runtime используют одно application core;
- focused tests, full Docker pytest и Graphify update проходят;
- isolated production-parity replay проходит на фактическом production provider/model/auth без customer delivery;
- production не изменён до явного approval;
- после rollout все application-plane containers имеют один immutable release ID.
