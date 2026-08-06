# Демо для коллег — как запустить

Работающий вертикальный срез v1 (и всё, что достроено сверху): Channel -> Triage Agent ->
Diagnostic Collector -> (при эскалации) Bug Report Composer -> Knowledge Curator -> Approval
Gate -> публикация в Knowledge Base, плюс Incident Coordination и Analytics/CSAT. Всё на
живом Anthropic API и локальном Postgres. Подробности архитектуры — `docs/architecture.md`.

## 1. Перед демо (один раз на машине)

Нужны: Python 3.12+, `.venv` с зависимостями (`pip install -e ".[dev,demo]"`), `.env` с
`DATABASE_URL` и `ANTHROPIC_API_KEY` (см. `src/etiology/config.py` — файл не в git,
пример структуры смотри там же), локально установленный PostgreSQL 17
(`C:\Program Files\PostgreSQL\17\bin`).

```bash
"/c/Program Files/PostgreSQL/17/bin/initdb.exe" -D "$(pwd)/.pgdata_local/data" -U postgres -E UTF8 --auth trust
bash scripts/db_start.sh
.venv/Scripts/python.exe scripts/migrate.py           # нужен DATABASE_URL суперпользователя, см. ниже
"/c/Program Files/PostgreSQL/17/bin/psql.exe" -U postgres -h 127.0.0.1 -p 5433 -d etiology -c "CREATE ROLE etiology_app LOGIN PASSWORD '<из .env DATABASE_URL>' NOBYPASSRLS;"
"/c/Program Files/PostgreSQL/17/bin/psql.exe" -U postgres -h 127.0.0.1 -p 5433 -d etiology -f scripts/grant_app_role.sql
```

(На этой машине всё это уже сделано — см. память проекта `project_local_postgres_setup`.
Ниже — то, что реально нужно перед КАЖДЫМ показом.)

## 2. Перед каждым показом

```bash
bash scripts/db_start.sh                                # поднять локальный Postgres (не автостартует)
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/seed_demo.py   # идемпотентно, безопасно перезапускать
```

## 3. Способ показа через браузер (рекомендуется)

```bash
bash scripts/db_start.sh
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m streamlit run scripts/demo_ui.py
```

Откроется браузер на `http://localhost:8501` — слева навигация по 4 экранам: Пайплайн
(ввести обращение клиента, увидеть шаги пайплайна вживую), Approval Gate (очередь,
approve/reject, публикация одобренных статей KB), Массовый сбой (Incident Coordination),
Аналитика. Ниже — те же сценарии через терминал, для скриптового прогона без интерфейса.

## 4. Сценарии для показа (в терминале, реальные вызовы Anthropic API — каждый ~10-60 сек)

**Сценарий A — самозакрытие по базе знаний** (Triage сам находит решение):
```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/demo.py "Здравствуйте, у нас на трекере вдруг браузер стал ругаться на сертификат, пишет что соединение не защищено" 5
```
Показывает: triage -> kb_closable=true -> инцидент закрыт без участия человека, плюс CSAT
и аналитику (топ тем, resolution rate) в конце.

**Сценарий B — честная эскалация без выдумывания** (полный цикл до готовой публикации):
```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/demo.py "СРОЧНО! Трекер полностью лёг, весь трафик по всем офферам сыпется с ошибкой 500, конверсии не считаются вообще нигде"
```
Показывает: triage -> нет команды в каталоге -> честная эскалация на человека (не
изобретает команду) -> Bug Report Composer собирает тех.спецификацию (явно помечает, каких
данных не хватает) -> Knowledge Curator предлагает статью в очередь Approval Gate.

**Сценарий C — массовый сбой, Incident Coordination**:
```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/demo_mass_outage.py
```
Показывает: три независимых обращения про одну аварию -> агент группирует их в один
master-инцидент вместо трёх параллельных расследований.

**Замкнуть Approval Gate живьём** (после сценария B, чтобы показать
"черновик -> человек -> публикация" целиком): проще всего через веб-интерфейс (шаг 3) —
страница Approval Gate, кнопки "Утвердить" -> "Опубликовать в базу знаний". Через MCP
напрямую — инструменты `approval_gate_approve` -> `kb_publish_approved`
(`src/etiology/main.py:build_app()` собирает готовый MCP-сервер со всеми инструментами).

## 5. Если что-то не подключается

- `Не удалось подключиться к локальной БД` -> `bash scripts/db_start.sh`
- Демо-тенант не найден -> `scripts/seed_demo.py` (см. шаг 2)
- Postgres — это отдельный dev-кластер проекта на порту 5433 (`.pgdata_local/`), не системный
  сервис на 5432 — не автостартует при перезагрузке машины.

## 6. Известные, осознанные ограничения (говорить прямо, не прятать)

- **Один AI-провайдер** (Anthropic) — абстракция `ModelGateway` готова под fallback между
  провайдерами, но второй провайдер не подключён (нет ключа). Жёсткое правило Apliteni
  "минимум 2 провайдера" пока не выполнено по факту.
- Diagnostic Collector находит команду из каталога, только если `topic_tag` от Triage (
  свободный текст LLM) достаточно совпадает с вручную заведённым `scenario` — воспроизводится
  не при каждом запуске одного и того же сообщения.
- Approval Gate публикует одобренные статьи только для Knowledge Curator (`kb_suggestion`);
  для одобренного post-mortem аналогичного шага публикации пока нет.
