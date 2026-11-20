# 19.11.2026 — подготовка стенда DataLens OSS (стенд не запускался)

Стенд запущен позже, 20.11.2026: [протокол прогона](2026-11-20-stand.md). Ниже — состояние на 19.11, до первого прогона.

Подготовлены файлы для автоматической проверки витрины в открытом DataLens: в GitHub Codespaces или на любой Linux-машине с Docker. DataLens при подготовке не запускался ни разу. Всё ниже написано по документации и исходному коду релиза, поэтому работу стенда, API-запросов и карточек подтвердит или опровергнет только первый прогон `stand/run.sh`.

## Что подготовлено

| Файл | Назначение |
|---|---|
| [.devcontainer/devcontainer.json](../../.devcontainer/devcontainer.json) | Codespace: Ubuntu 24.04 по digest, docker-in-docker 4.1.3, Python 3.11 (фича python 1.8.1), sshd 1.1.0; 4 ядра, 16 ГБ, 32 ГБ |
| [stand/docker-compose.yml](../../stand/docker-compose.yml) | DataLens v2.9.0 без авторизации, temporal и демо-данных плюс ClickHouse 24.8.14.39 в той же сети |
| [stand/.env.example](../../stand/.env.example) | Имена секретов; `run.sh` создаёт `stand/.env` со случайными значениями |
| [stand/clickhouse/users.d/datalens.xml](../../stand/clickhouse/users.d/datalens.xml) | Пользователь ClickHouse `datalens` с профилем `readonly` для подключения |
| [stand/run.sh](../../stand/run.sh) | Весь прогон без участия человека, итог — `stand/out/SUMMARY.txt` |
| [stand/datalens_stand.py](../../stand/datalens_stand.py) | Подключение, датасеты, карточки, дашборд через API; чтение чисел и сверка |
| [stand/screenshots.py](../../stand/screenshots.py) | Снимки страниц через Playwright Chromium, без кликов |
| [src/marketing_funnel/stand_check.py](../../src/marketing_funnel/stand_check.py) | Ожидаемые значения карточек из эталона и сравнение «ожидалось / получено» |
| [tests/test_stand_check.py](../../tests/test_stand_check.py), [tests/test_stand_files.py](../../tests/test_stand_files.py) | Сравнение ловит испорченное число; статические проверки compose, `run.sh`, devcontainer |

## Шаги `run.sh` и способ автоматизации

| Шаг | Как сделан | Проверено до прогона |
|---|---|---|
| 1. `nproc`, `free -g`, `df -h`; стоп при нехватке диска (по умолчанию меньше 10 ГБ под каталогом Docker) | bash | `bash -n` в CI |
| 2. `docker compose up -d --wait` | compose | разбор YAML, закреплённые образы — тесты |
| 3. Снимок `--seed 20261031 --size 120`, сверка SHA-256 `inputs.json` и `report.json` с манифестом эталона (`python -m marketing_funnel.stand_check`), загрузка `scripts/load_clickhouse.py`; всем командам проекта передаётся `PYTHONPATH=src` | команды проекта | загрузчик проверен 18.11 на ClickHouse 24.8.14.39; сверка хешей и запуск загрузчика без установленного пакета — модульные тесты с 19.11 |
| 4. Рабочая книга, подключение ClickHouse, датасеты `channel_metrics` и `user_funnel`, 4 таблицы-карточки, дашборд с селектором модели | HTTP API (Python, стандартная библиотека) | не проверено |
| 5. Числа из DataLens: по каждой из трёх моделей каналы (подписки, выручка, себестоимость, расходы, маржа в рублях, CPA), доля без атрибуции; воронка | data API DataLens, сравнение с [report.json](2026-10-31-synthetic/report.json) | функция сравнения — модульные тесты |
| 6. Сборка каждой карточки через `/api/run` (успех — HTTP 200 и непустой `data.rows`), снимки страниц (успех — HTTP-статус ниже 400, у дашборда ещё класс `dash-body-loaded`), `docker stats --no-stream`, `docker compose logs`, `SUMMARY.txt` со строкой `overall:` | API, Playwright | на стенде не проверено; правила успеха — модульные тесты на подставных ответах с 19.11 |
| 7. `docker compose down -v` | `trap` в bash | тест на наличие |

Селектор модели в числах моделируется фильтром `model = <модель>` в запросе к data API. Это тот же фильтр, который на дашборде задаёт селектор, но клик по селектору в интерфейсе скрипт не делает. На снимках дашборд открывается с параметром URL для моделей `linear` и `time_decay`; такой способ переключения селектора не подтверждён.

Коды выхода: 0 — числа совпали, каждая карточка вернула строки таблицы, каждая страница для снимков ответила статусом ниже 400 (с `SKIP_SCREENSHOTS=1` код 0 означает только числа и карточки, это видно по строке `overall:` в `SUMMARY.txt`); 1 — числа расходятся (таблица «ожидалось / получено»); 2 — сбой API или карточка без строк; 3 — мало диска или нет инструмента; 4 — не удались снимки; 5 — SHA-256 снимка разошёлся с манифестом эталона; 6 — стенд не поднялся или витрина не загрузилась. Ни один код не получен на практике: стенд не запускался.

## Источники и что из них взято

Релиз — `datalens-tech/datalens` [v2.9.0](https://github.com/datalens-tech/datalens/releases/tag/v2.9.0) от 19.02.2026, последний на 19.11.2026 по списку релизов GitHub.

- [docker-compose.yaml](https://github.com/datalens-tech/datalens/blob/v2.9.0/docker-compose.yaml) — образы и переменные окружения сервисов; [versions-config.json](https://github.com/datalens-tech/datalens/blob/v2.9.0/versions-config.json) — ui 0.3831.0, us 1.39.0, backend 0.2457.0, auth 0.27.0, meta-manager 0.52.0.
- [init.sh](https://github.com/datalens-tech/datalens/blob/v2.9.0/init.sh) — что меняют `--disable-auth` (`AUTH_ENABLED=false`, `AUTH_TYPE=NONE`, ключи `-`, сервис auth не поднимается), `--disable-temporal` и `--disable-demo`; генерация секретов; `--dev-expose-ports` публикует control-api на 8010 и data-api на 8020.
- [README.md](https://github.com/datalens-tech/datalens/blob/v2.9.0/README.md) — минимум 4 ГБ RAM и 2 ядра; ui 512 МБ, data-api 1 ГБ, control-api, us и postgres по 512 МБ; интерфейс на порту 8080; при включённой авторизации вход `admin` / `admin` в быстром старте; без `HC=1` используется D3 с ограниченным набором визуализаций.

Вход: в стенде авторизация выключена так же, как делает `init.sh --disable-auth`, поэтому логин не нужен. В авторизации по умолчанию стенд не проверяется.

Пути API — из исходников `datalens-tech/datalens-ui` [v0.3831.0](https://github.com/datalens-tech/datalens-ui/tree/v0.3831.0) и `datalens-tech/datalens-backend` [v0.2457.0](https://github.com/datalens-tech/datalens-backend/tree/v0.2457.0):

| Запрос | Источник | Статус |
|---|---|---|
| `POST /api/v1/connections` (control-api), тело `{name, type: "clickhouse", workbook_id, host, port, username, password, db_name, secure, readonly}` | backend `lib/dl_api_lib/dl_api_lib/app/control_api/resources/connections.py` и `resources/__init__.py` (префикс `/api/v1`); поля — `lib/dl_api_lib_testing/dl_api_lib_testing/connection_base.py`, `lib/dl_connector_clickhouse/dl_connector_clickhouse_tests/db/api/base.py`; `workbook_id` или `dir_path` обязателен по `lib/dl_api_connector/dl_api_connector/api_schema/top_level.py` | путь и поля из тестов backend; на стенде не проверено |
| `POST /api/v1/datasets/validators/dataset` с `updates` `add_source` (`source_type: "CH_TABLE"`, `parameters: {db_name, table_name}`) и `add_source_avatar`; затем `update_field` и `add_field` с формулами | backend `lib/dl_api_lib/dl_api_lib/app/control_api/resources/dataset.py`, `schemas/action.py`, `schemas/validation.py`; `lib/dl_api_client/dl_api_client/dsmaker/api/dataset_api.py`, `serialization_base.py`; `dl_connector_clickhouse/core/clickhouse/constants.py` | не проверено |
| `POST /api/v1/datasets` с `{dataset, name, workbook_id, preview}` | backend `schemas/main.py` (`CreateDatasetSchema`), `dsmaker/api/dataset_api.py` | не проверено |
| `POST /api/data/v2/datasets/{id}/result`, поля `{ref: {type: "id", id}, role_spec: {role: "row"}}`, фильтр `{ref, operation: "EQ", values}`, ответ `result_data[].rows[].data` | backend `lib/dl_api_lib/dl_api_lib/app/data_api/app.py`, `schemas/data.py`, `lib/dl_constants/dl_constants/enums.py` | не проверено |
| Готовность: `GET /api/v1/ping` (control-api), `GET /ping` (data-api, ui) | backend `lib/dl_api_lib/dl_api_lib/app/control_api/resources/monitoring.py`, `lib/dl_api_lib/dl_api_lib/app/data_api/app.py`; ui `src/server/modes/opensource/routes.ts` | не проверено |
| `POST /gateway/root/us/createWorkbook` `{title}` | ui `routes.ts` (`POST /gateway/:scope/:service/:action?`), `src/server/nodekit.ts` (scope `root`), `src/shared/schema/us/actions/workbooks/create-workbook.ts`; склейка URL в `@gravity-ui/sdk` не проверена | не проверено |
| `POST /api/charts/v1/charts` `{template: "datalens", name, workbookId, data, annotation}` | ui `src/shared/old-schema/charts.ts`, `src/server/components/charts-engine/controllers/charts.ts` | не проверено |
| Конфигурация карточки `flatTable` версии 4 | демо-данные релиза `postgres/demo-data/us-d3-data.sql`; ui `src/shared/modules/config/wizard/mapChartsConfigToLatestVersion.ts` приводит старые версии к новой | форма поля взята из демо-записи; не проверено |
| `POST /gateway/root/mix/createDashboardV1` `{entry: {workbookId, name, meta, data}, mode: "publish"}` | ui `src/shared/schema/mix/actions/dash/create-dashboard-v1.ts`, `src/shared/zod-schemas/dash.ts` | схема из zod; не проверено |
| `POST /api/run` `{id, workbookId, params}` | ui `src/server/components/charts-engine/controllers/run.ts` | не проверено |
| Ожидание дашборда по классу `dash-body-loaded` | ui `src/shared/constants/dash.ts` (`LOADED_DASH_CLASS`) | не проверено |

Без заголовков авторизации запросы проходят при `AUTH__TYPE=NONE` (control-api и data-api работают с хранилищем по мастер-токену) и `AUTH_ENABLED=false` в ui — так следует из кода backend и ui. CSRF-проверку в шлюзе ui исходник не включает явно; это не проверено.

Публичный API `/rpc/:action` с заголовком `x-dl-api-version` в исходниках ui есть, но в маршрутах opensource-сборки не зарегистрирован, поэтому не используется.

## Образы

Digest взяты 19.11.2026 из реестров (ghcr.io и Docker Hub), размер — сумма сжатых слоёв linux/amd64:

| Образ | Сжатый размер |
|---|---|
| `ghcr.io/datalens-tech/datalens-postgres:16@sha256:37ba1c53…` | 199 МБ |
| `ghcr.io/datalens-tech/datalens-control-api:0.2457.0@sha256:1fa41630…` | 187 МБ |
| `ghcr.io/datalens-tech/datalens-data-api:0.2457.0@sha256:c7448f7d…` | 187 МБ |
| `ghcr.io/datalens-tech/datalens-us:1.39.0@sha256:9f13b3ce…` | 261 МБ |
| `ghcr.io/datalens-tech/datalens-ui:0.3831.0@sha256:5c720832…` | 227 МБ |
| `clickhouse/clickhouse-server:24.8.14.39@sha256:1ffa82ed…` | 179 МБ |

Итого около 1,24 ГБ сжатыми; общие слои control-api и data-api не вычитались. Размер распакованных образов не измерялся. Тег `16` у datalens-postgres не привязан к релизу; digest зафиксирован на дату подготовки. Порог свободного диска в `run.sh` — 10 ГБ: это запас под распаковку, Chromium Playwright и данные, а не измерение. Память: минимум DataLens по README — 4 ГБ, ClickHouse добавит своё; Codespace на 16 ГБ.

## Что написано вслепую и скорее всего сломается

1. Тела запросов создания датасета: обязательные поля, формат `raw_schema` после `add_source`, агрегация полей, синтаксис формул (`IF(..., NULL)`, деление).
2. Создание рабочей книги через шлюз ui и путь `/gateway/root/...`.
3. Конфигурации карточек: форма элементов `flatTable` и сортировки взята из демо-записи версии 4.
4. Дашборд: схема `createDashboardV1`, связь селектора с карточками, параметр URL для смены модели.
5. Снимки: ожидание загрузки, время сборки страниц.
6. Пользователь ClickHouse из `users.d` с паролем `from_env` и подключение с `readonly: 1`.
7. Готовность сервисов: у control-api, data-api, us и ui в релизном compose нет healthcheck, поэтому `run.sh` после `--wait` ещё ждёт `ping` до 10 минут.

Каждый ответ API сохраняется в `stand/out/api/` — по ним можно исправить запросы после первого прогона.

## Проверки до прогона стенда

Полный прогон CI `marketing-funnel-datalens_20261119-094449` на коммите `57be7a4`: обе задачи — `JOB_ok` (`unit` — 21 с, 28 тестов; `clickhouse` — 17 с, 5 тестов). Итог взят из `state/summary.tsv` и журналов шагов; они вложены в [архив журналов](2026-11-19-ci.zip), папка `20261119-094449` (журналы установки зависимостей не вложены). Стенд DataLens в этом прогоне не запускался: тесты читают файлы стенда и не поднимают его контейнеры.

Правки после второго аудита и новый полный прогон — в [отдельном протоколе](2026-11-19-second-audit.md).
