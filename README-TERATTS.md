# TeraTTS Provider для Omarchy Linux

Нативная интеграция вашего сервера **TeraTTSv2** в систему экранного озвучивания и чтения выделенного текста **Omarchy** (`omarchy-tts`).

## Архитектура

```text
Выделение текста в любом окне (браузер, терминал, Neovim, Telegram)
                            ↓
               Хоткей Hyprland (Super + Alt + E)
                            ↓
               bin/speak (Omarchy TTS)
                            ↓
              providers/teratts
                            ↓
    ┌───────────────────────┴───────────────────────┐
    │  1. Асимметричный чанкинг по предложениям     │
    │     (Чанк 0 = 240 симв. для мгновенного TTFA) │
    │  2. duration_scale = 1.0 / TTS_RATE           │
    │  3. text_mode = "russian_only"                │
    │  4. speech_front = true (lexicon.toml)        │
    └───────────────────────┬───────────────────────┘
                            ↓
          POST https://teratts.tail9fd337.ts.net/tts
             (или http://127.0.0.1:8088/tts)
                            ↓
               Потоковый PCM 44.1 kHz WAV
                            ↓
          pw-cat / aplay (PipeWire / ALSA)
```

---

## Вариант 1: Быстрая установка без форка (Custom Provider)

Если на вашем ноутбуке Omarchy уже установлен плагин `omarchy-tts` из официального каталога, вам **не нужно менять плагин**. Достаточно просто скопировать файл провайдера:

```bash
# 1. Создаём директорию пользовательских провайдеров
mkdir -p ~/.config/omarchy-tts/providers

# 2. Копируем скрипт teratts
cp providers/teratts ~/.config/omarchy-tts/providers/teratts
chmod +x ~/.config/omarchy-tts/providers/teratts
```

---

## Вариант 2: Полная установка с поддержкой `# preprocess: raw`

В этом репозитории в `bin/speak` добавлена нативная поддержка директивы `# preprocess: raw`, которая автоматически защищает технический текст (UUID, версии ПО, URL, программные идентификаторы `snake_case`, математические знаки) от искажения сторонним текстовым санитайзером.

Для установки репозитория как основного плагина Omarchy:

```bash
# Симлинк в каталог плагинов Omarchy
ln -s "$PWD" "${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/io.github.hikari112.tts"
```

---

## Настройка авторизации и подключения

### 1. Токен доступа (Bearer Token)

`teratts-server` требует авторизацию через заголовок `Authorization: Bearer <token>`.

Вы можете задать токен **любым из двух способов**:

* **Через системный Keyring (рекомендуется, безопасно):**
  ```bash
  secret-tool store --label='teratts' service omarchy-tts key teratts
  # Введите ваш токен при запросе
  ```

* **Через переменную окружения:**
  Добавьте в `~/.bashrc` или `~/.config/hypr/hyprland.conf`:
  ```bash
  export TERATTS_BEARER_TOKEN="ваш_секретный_токен"
  ```

### 2. URL сервера TeraTTS

По умолчанию провайдер обращается к узлу Tailscale:
`https://teratts.tail9fd337.ts.net`

Если сервер запущен локально на ноутбуке или на другом порту, переопределите его:
* Через конфиг:
  ```bash
  speak --set .teratts.endpoint "http://127.0.0.1:8088"
  ```
* Или через переменную окружения:
  ```bash
  export TERATTS_URL="http://127.0.0.1:8088"
  ```

---

## Проверка работоспособности

```bash
# 1. Проверяем, что teratts появился в списке провайдеров
speak --list

# 2. Делаем teratts активным провайдером
speak --set .provider teratts

# 3. Подгружаем актуальный список голосов с сервера
speak --refresh-voices teratts

# 4. Проверяем статус в выводе --info
speak --info | jq '{provider: .provider, voice: .voice, voices: .voices}'

# 5. Тестовая озвучка
speak "Привет! Динамическое выделение ресурсов работает в Kubernetes 1.35."
```

---

## Горячие клавиши в Hyprland (`hyprland.conf`)

Omarchy TTS по умолчанию использует следующие привязки (вкладка **Keys** в панели управления или файл `~/.config/hypr/hyprland.conf`):

| Комбинация | Команда | Действие |
|---|---|---|
| `SUPER + ALT + E` | `speak --selection` | **Озвучить выделенный текст** (повторное нажатие — Stop) |
| `SUPER + ALT + A` | `speak --clipboard` | Озвучить текст из буфера обмена (или OCR скопированной картинки) |
| `SUPER + ALT + X` | `speak --stop` | **Мгновенно прервать воспроизведение** |
| `SUPER + ALT + R` | `speak --snip` | Выделить область экрана мышью, распознать через OCR и озвучить |
| `SUPER + ALT + W` | `speak --window` | Озвучить активное окно через OCR |

Пример секции в `~/.config/hypr/hyprland.conf`:
```ini
bind = SUPER ALT, E, exec, speak --selection
bind = SUPER ALT, A, exec, speak --clipboard
bind = SUPER ALT, X, exec, speak --stop
```

---

## Особенности и гарантии

1. **Сохранность буфера обмена**: при вызове `speak --selection` текст считывается напрямую через протокол Wayland PRIMARY selection (`wl-paste --primary`). Пользовательский Clipboard не перезаписывается и не портится.
2. **Низкая задержка первого звука**: длинные абзацы автоматически разбиваются на связные предложения. Первый фрагмент (до 240 символов) синтезируется и начинает звучать за 300–500 мс, пока последующие чанки генерируются в фоне.
3. **Бесшовное воспроизведение**: аудиопоток непрерывно направляется в PipeWire (`play_raw 44100 1`), исключая щелчки и паузы между чанками.
4. **Регулировка скорости**: параметр скорости `speak -r 1.5` корректно пересчитывается в `duration_scale = 1.0 / rate`, обеспечивая естественное ускорение без пересинтеза.
