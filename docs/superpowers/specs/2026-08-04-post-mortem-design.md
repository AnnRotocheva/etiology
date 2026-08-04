# Design: Post-mortem Agent (v1)

**Дата:** 2026-08-04
**Статус:** утверждён, готов к реализации (решения по реализации приняты автономно)

Ссылка на архитектуру: `docs/architecture.md` §6.2 (Post-mortem Agent).

## Контекст

Второй агент домена Escalation & Sync, естественное продолжение уже построенного по форме:
читает трейл инцидента через существующий `EventReader.read_aggregate_events` (таймлайн — это и
есть упорядоченный список событий, ничего нового читать не нужно), публикует черновик только
через уже существующий `ApprovalGate` (не в Event Store напрямую, не публикует сама — «только
черновик»). Ни одной новой платформенной зависимости, в отличие от предыдущих агентов.

## Решения

- **Только для критических инцидентов — жёсткая проверка в коде, не только описание.**
  Архитектура говорит «по закрытию критического инцидента» — это не совет, а граница агента:
  если `severity` триажа не `critical`, `PostMortemError`, а не тихая генерация post-mortem для
  любого инцидента.
- **«Закрытие»** — то же терминальное событие, что и у Curator: последнее `bug_report.created`
  либо `incident.resolved`. Если ни того ни другого нет — инцидент ещё не закрыт,
  `PostMortemError`.
- **Модель — STRONG-tier**, явно зафиксировано архитектурой («качество текста влияет на доверие
  команды к разбору»).
- **Таймлайн строится из самого трейла событий**, включая `incident.merged`/
  `incident.status_published`, если инцидент — master объединённой группы (Incident
  Coordination) — это реальный, уже присутствующий в Event Store контекст импакта, не
  выдумывается заново.
- **Root cause — только если подтверждена в трейле.** Bug Report Composer публикует
  диагностику, но не подтверждённую причину (девелоперы ещё не чинили) — модель обязана явно
  писать «причина не подтверждена, требуется дальнейшее расследование», если в трейле нет
  подтверждения, а не изобретать правдоподобную причину (тот же анти-галлюцинаторный принцип,
  что и во всех предыдущих агентах).
- **Без реальной публикации в Slack-канал `keitaro-internal-updates`** — упомянутого в
  архитектуре канала нет в кодовой базе (Slack-интеграции не существует), тот же принцип, что и
  у `ApprovalGate`/Bug Report Composer: `ApprovalGate.submit()` — artifact v1, реальная доставка
  в канал — отдельная будущая интеграция.
- **Двойная запись, как и у Curator:** `ApprovalGate.submit(..., "post_mortem", ...)` — очередь
  для человека, плюс `post_mortem.drafted` в Event Store — аудит-лог. В отличие от
  `kb_suggestion.created` (новый самостоятельный aggregate), post-mortem — артефакт **об одном
  конкретном инциденте** без своего отдельного жизненного цикла, поэтому событие публикуется на
  `aggregate_type="incident"`, `aggregate_id=incident_id` — тот же паттерн, что и у
  `bug_report.created`.

## Компоненты

### Post-mortem Agent (`domain/escalation_sync/post_mortem`)

```python
async def draft_post_mortem(
    tenant_id: str,
    incident_id: str,
    *,
    gateway: ModelGateway,
    approval_gate: ApprovalGate,
    publisher: EventPublisher,
    read_aggregate_events=EventReader().read_aggregate_events,
) -> PostMortemResult
```

1. `events = await read_aggregate_events(tenant_id, "incident", incident_id)`.
2. `triaged = _find_latest(events, "incident.triaged")`. Если `triaged is None` или
   `triaged.payload.get("severity") != "critical"` — `PostMortemError`.
3. `terminal = _find_latest(events, "bug_report.created") or _find_latest(events, "incident.resolved")`.
   Если `None` — `PostMortemError` (инцидент ещё не закрыт).
4. STRONG-tier запрос: system-промпт с таймлайном (все события трейла — тип, время, ключевые
   поля payload) + инструкция не изобретать root cause, если он не подтверждён в трейле. JSON:
   `{"title": "...", "timeline": ["..."], "hypotheses": ["..."], "root_cause": "...",
   "impact": "...", "action_items": ["..."]}`. Markdown-fence-strip + один retry, как везде.
5. `approval_id = await approval_gate.submit(tenant_id, "post_mortem", {...все поля...},
   created_by="post_mortem_agent")`.
6. Публикация `post_mortem.drafted` (`aggregate_type="incident"`, `aggregate_id=incident_id`).
7. Возврат `PostMortemResult(incident_id, approval_id, title, timeline, hypotheses, root_cause,
   impact, action_items)`.

## Тестирование

Юнит-тесты: успешный черновик для критического закрытого инцидента; отказ на некритичном
severity; отказ на незакрытом инциденте; retry на невалидный JSON; двойной сбой → исключение.
Все с `FakeProvider`/фейковыми `read_aggregate_events`/`FakeApprovalGate`/`FakePublisher` — без
новых DB-зависимостей (EventReader/ApprovalGate/EventPublisher уже протестированы отдельно).
Ручная проверка на живом API после выполнения плана.

## Коммит

Один коммит — новых платформенных зависимостей нет, только сам агент.
