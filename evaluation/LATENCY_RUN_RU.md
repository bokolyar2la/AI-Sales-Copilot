# Как повторить измерения

Это отдельный платный эксперимент на синтетических репликах. Основная конфигурация DeepSeek и рабочая база разговоров не меняются. Сервер эксперимента слушает только 127.0.0.1:8877; SQLite находится в ../../work/latency-experiment.sqlite.

## Подготовка

В корне проекта уже должен работать .env для DeepSeek. Не копируйте ключ в скрипты. Соберите frontend:

```powershell
cd frontend
npm run build
cd ..
```

Для браузерного стенда нужен Microsoft Edge и Playwright. Установите Playwright в отдельную папку инструментов вне проекта:

```powershell
New-Item -ItemType Directory -Force ../../work/latency-browser
cd ../../work/latency-browser
npm init -y
npm install playwright --no-audit --no-fund
```

## Сервер, терминал 1 в корне проекта

```powershell
$env:COPILOT_LATENCY_EXPERIMENT='1'
.\.venv\Scripts\python.exe -m uvicorn evaluation.latency_server:app --host 127.0.0.1 --port 8877 --log-level warning
```

## Браузерный прогон, терминал 2 в корне проекта

Сохраните прежний evaluation/latency-results.json под другим именем: прогон перезапишет его.

```powershell
$env:PLAYWRIGHT_MODULE=(Resolve-Path ../../work/latency-browser/node_modules/playwright).Path
node --preserve-symlinks --preserve-symlinks-main evaluation/run_latency.cjs
```

Прогон делает 32 контрольных обращения приложения; исправляющие попытки могут увеличить число платных вызовов. Учитывается первый непустой фрагмент SSE, а не пустое событие с ролью. Частичная фраза в интерфейс не отправляется.

После прогона остановите экспериментальный сервер Ctrl+C. Рабочий сервер 8765 трогать не нужно.

## Анализ

```powershell
.\.venv\Scripts\python.exe evaluation/summarize_latency.py
```

latency-quality-review.json содержит ручные оценки конкретного сохранённого прогона. При повторе их нужно пересмотреть, а не считать оценками новых фраз. Агрегатор проверяет привязку оценки к тексту рекомендации.

В обычном приложении, после перезапуска и Ctrl+F5, числовые образцы доступны в консоли браузера:

```javascript
window.__copilotDiagnostics
```

Поле display_proxy_ms — от отправки запроса до двух кадров после React. Это не инструмент физического измерения монитора. Для скрытой вкладки visible=false; такие образцы не стоит смешивать с активной вкладкой.

Серверный X-Copilot-Diagnostics включает stages_ms, calls, request_count, retry_count, retry_request_ms и validations. Метрики engine сохраняются вместе с рекомендацией; длительность последующей записи и ответа есть только в итоговом HTTP-снимке.

Экспериментальный импорт latency_server подменяет выбор знаний только в своём процессе, чтобы и LLM, и валидатор получили один синтетический каталог. В обычном запуске этот модуль не импортируется.
