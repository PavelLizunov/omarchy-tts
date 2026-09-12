# Инструкция для локального AI-агента на ноутбуке Omarchy Linux

Этот документ предназначен для AI-агента (Claude Code / OpenCode / Cursor / DSH / Shell Agent), запущенного непосредственно на целевом ноутбуке с **Omarchy Linux**.

---

## 1. Контекст и архитектура

### Целевое окружение
- **ОС**: Omarchy Linux (Arch-based).
- **Оконный менеджер**: Hyprland (Wayland).
- **Звуковой сервер**: PipeWire (`pw-cat`, `play_raw 44100 1`).
- **Считывание выделения**: `wl-paste --primary` (протокол Wayland PRIMARY selection, буфер обмена не затирается).

### Репозиторий
- **Форк**: [https://github.com/PavelLizunov/omarchy-tts](https://github.com/PavelLizunov/omarchy-tts) (ветка `main`).
- **Базовый проект**: `hikari112/omarchy-tts` (v1.3.2, коммит `f7d4df81`).

### TTS-бэкенд (TeraTTSv2)
- **Сервер**: `teratts-server` (Rust + ONNX TeraTTSv2 + ruaccent + speechfront + lexicon.toml).
- **Сетевой адрес в Tailscale**: `https://teratts.tail9fd337.ts.net` (или `http://127.0.0.1:8088` если запущен локально).
- **Эндпоинты**:
  - `GET /health` — проверка статуса (возвращает `status: "ready"`, список голосов `ru_f1`, `ru_f2`, `ru_m1`, `ru_m5`, `eng_f3`..`eng_m4`).
  - `POST /tts` — генерация WAV (mono 16-bit PCM 44.1 kHz). Требует заголовок `Authorization: Bearer <TOKEN>`.

---

## 2. Что уже реализовано и протестировано в репозитории

В форке `PavelLizunov/omarchy-tts` выполнены и покрыты тестами следующие задачи:

1. **`providers/teratts`**:
   - Автоматический опрос `GET /health` по флагу `--voices` для формирования каталога голосов Omarchy.
   - Извлечение Bearer-токена из системного keyring через `secret-tool` (`service: omarchy-tts, key: teratts`) или переменной `TERATTS_BEARER_TOKEN`.
   - Санитаризация входного текста:
     - Очистка `\r\n` $\to$ `\n`, удаление control-символов (`\x00-\x1f`), которые ломали `russian_only` режим сервера.
     - Замена `_` в `snake_case` на пробелы для раздельного произношения идентификаторов кода.
     - Вырезание вредоносных HTML-тегов (`<br>`, `<li>`, `<tr>`), вызывавших HTTP 400, с сохранением целевых языковых тегов `<ru>` и `<en>`.
     - Преобразование операторов `<=` $\to$ `меньше или равно`, `>=` $\to$ `больше или равно`, `<` $\to$ `меньше`, `>` $\to$ `больше`, чтобы угловые скобки не путали парсер тегов.
   - **Асимметричный чанкинг по предложениям**:
     - Чанк 0 = до 240 символов (обеспечивает задержку до первого звука 300–500 мс).
     - Чанк 1 = до 480 символов.
     - Чанки 2+ = до 2000 символов (гарантированный hard-cut для токенов без пробелов >2000 символов).
   - **Инверсия скорости**: `duration_scale = 1.0 / TTS_RATE` с ограничением в диапазоне `[0.25, 4.0]`.
   - **Надёжный WAV-парсинг**: поиск чанка `data` по заголовкам вместо предположения о строго 44-байтовом заголовке.
   - **Обработка BrokenPipeError**: при остановке звука пользователем процесс завершается с кодом 0, не загрязняя stderr трейсбеками и не переводя статус провайдера в `failing`.
   - Маппинг кодов ошибок: `77` (401/403 auth), `75` (429 rate-limit), `74` (500/502/503/504 / network error).

2. **`bin/speak`**:
   - Реализована директива метаданных `# preprocess: raw` — при её наличии `speak` пропускает деструктивный внешний санитайзер `lib/sanitize.py`, чтобы технический текст (UUID, версии, программные имена) поступал напрямую в родной `speechfront` сервера TeraTTS.
   - Добавлена нативная поддержка провайдера `teratts` в выбор голоса по умолчанию (`voice_for_provider`), в обновление каталога голосов (`refresh_remote_voices`) и в сбор информации (`emit_info`).
   - Добавлена инвалидация кэша health-чеков для `.teratts.*` в `set_config`.

3. **QML-панель (`components/TtsController.qml`) и `bin/speak-setup`**:
   - В `TtsController.qml` методы `storeKey` и `removeKey` теперь разрешают `teratts` (ранее хардкод `["openai", "elevenlabs", "google", "gemini"]` молча блокировал сохранение токена через GUI).
   - В `bin/speak-setup` список `CLOUD_KEYS` дополнен провайдером `teratts`.

4. **Тесты**:
   - `tests/test_teratts.py`: 14 тестов (mock HTTP сервер, авторизация, инверсия rate, чанкинг, BrokenPipe, валидация HTML-тегов, raw-препроцессинг).
   - Полный регрессионный прогон: **243 теста успешно пройдены** (`Ran 243 tests in 54.955s — OK`).

---

## 3. Чек-лист задач для локального агента на ноутбуке

Агент на ноутбуке должен выполнить следующие шаги и подтвердить их работоспособность:

### Шаг 1. Развертывание репозитория
1. Клонировать или подтянуть ветку `main` из `https://github.com/PavelLizunov/omarchy-tts.git`:
   ```bash
   cd ~/.config/omarchy/plugins
   # Если плагин io.github.hikari112.tts уже существует:
   cd io.github.hikari112.tts
   git remote set-url origin https://github.com/PavelLizunov/omarchy-tts.git
   git pull origin main
   # Либо клонировать заново:
   # git clone https://github.com/PavelLizunov/omarchy-tts.git io.github.hikari112.tts
   ```
2. Убедиться, что `providers/teratts` и `bin/speak` имеют права на исполнение (`chmod +x`).

### Шаг 2. Проверка доступности сервера TeraTTS
1. Проверить доступ к серверу в Tailnet:
   ```bash
   curl -fsS https://teratts.tail9fd337.ts.net/health
   ```
   *Ожидается*: JSON со статусом `{"status":"ready", ...}` и списком голосов (`ru_f1`, `ru_m1` и т.д.).
2. Если используется локальный сервер на ноутбуке:
   ```bash
   speak --set .teratts.endpoint "http://127.0.0.1:8088"
   ```

### Шаг 3. Сохранение Bearer-токена
1. Сохранить токен в системный keyring:
   ```bash
   secret-tool store --label='teratts' service omarchy-tts key teratts
   # Ввести секретный токен сервера TeraTTS
   ```
   *Альтернатива (для отладки в терминале)*: `export TERATTS_BEARER_TOKEN="ваш_токен"`.

### Шаг 4. Настройка провайдера и голоса
1. Установить `teratts` активным провайдером:
   ```bash
   speak --set .provider teratts
   ```
2. Загрузить список голосов с сервера:
   ```bash
   speak --refresh-voices teratts
   ```
3. Проверить вывод `speak --info`:
   ```bash
   speak --info | jq '{provider: .provider, voice: .voice, voices: .voices}'
   ```
   *Ожидается*: `provider: "teratts"`, `voice: "ru_f1"`, непустой список `voices`.

### Шаг 5. Тестирование синтеза и звука
1. Запустить синтез короткой фразы в терминале:
   ```bash
   speak "Привет! Проверка звука через TeraTTS на ноутбуке Omarchy."
   ```
   *Ожидается*: чистый звук через PipeWire без падений и трейсбеков.
2. Проверить устойчивость к техническому тексту:
   ```bash
   speak "Dynamic Resource Allocation работает в Kubernetes 1.35 с переменной cluster_node_id."
   ```
   *Ожидается*: «cluster node id» читается раздельно, «1.35» и «Kubernetes» не искажаются санитайзером.

### Шаг 6. Проверка Wayland PRIMARY Selection
1. Выделить любой текст мышью в браузере или Neovim.
2. Запустить из терминала:
   ```bash
   speak --selection
   ```
3. Убедиться, что:
   - Прозвучал именно выделенный текст.
   - Содержимое основного буфера обмена (`wl-paste`) не изменилось и не перезаписалось.

### Шаг 7. Настройка и проверка горячих клавиш в Hyprland
1. Открыть `~/.config/hypr/hyprland.conf` (или файл биндингов `~/.config/hypr/bindings.conf`).
2. Проверить наличие или добавить привязки:
   ```ini
   # Озвучивание выделения (toggle: повторное нажатие останавливает)
   bind = SUPER ALT, E, exec, speak --selection

   # Озвучивание буфера обмена (текст или картинка через OCR)
   bind = SUPER ALT, A, exec, speak --clipboard

   # Мгновенная остановка речи
   bind = SUPER ALT, X, exec, speak --stop
   ```
3. Применить конфигурацию: `hyprctl reload`.
4. Проверить работу хоткеев в реальной сессии.

### Шаг 8. Проверка GUI / BarWidget
1. Нажать на иконку динамика в панели Omarchy (Waybar / QuickShell bar widget).
2. Убедиться, что в выпадающей панели:
   - Выбран провайдер **TeraTTS**.
   - Доступен браузер голосов и переключатель скорости.
   - Кнопка «Test» воспроизводит тестовую фразу.

---

## 4. Возможные проблемы и пути решения

| Симптом | Причина | Решение |
|---|---|---|
| `HTTP 401 Unauthorized` | Токен не задан или не совпадает | Проверить токен: `secret-tool lookup service omarchy-tts key teratts` или задать `export TERATTS_BEARER_TOKEN="..."` |
| `network error: Connection refused` | Сервер недоступен | Проверить Tailscale (`tailscale status`, `curl https://teratts.tail9fd337.ts.net/health`) |
| `pw-cat: not found` | Отсутствует утилита PipeWire | Установить PipeWire utils: `sudo pacman -S pipewire alsa-utils` |
| Выделение не читается | Не сработал PRIMARY буфер | Проверить `wl-paste --primary`. Если окно блокирует PRIMARY, использовать `speak --clipboard` |
