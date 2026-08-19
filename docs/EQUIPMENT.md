# Добавление оборудования (`equipment add`)

Оборудование — карточки техники с серийными/инвентарными номерами,
фотографиями и статусом, привязанные к адресу организации.

## Интерактивный мастер (визард)

```bash
python3 f2c_inventory.py equipment add --interactive
```

Мастер по шагам:

1. **Организация** — выбор из списка (стрелки ↑/↓ + фильтр);
2. **Адрес размещения** — выбор из адресов организации (живой фильтр);
3. **Поля карточки** (пустое поле пропускается):
   - Название / модель — обязательно;
   - Серийный номер;
   - Инвентарный номер;
   - Количество;
   - Комментарий.

Перед отправкой запрос показывается целиком и запрашивает подтверждение.

## Неинтерактивно (для скриптов)

```bash
# JSON
python3 f2c_inventory.py equipment add \
    -d '{"name": "Ноутбук HP 250", "serial_number": "SN123", "address_id": 101}'

# key=value (числа и true/false распознаются автоматически)
python3 f2c_inventory.py equipment add \
    -d name="Ноутбук HP 250" -d serial_number=SN123 -d address_id=101

# из файла
python3 f2c_inventory.py equipment add -d @new_equipment.json

# не отправлять, только показать запрос
python3 f2c_inventory.py equipment add -d '{"name":"Тест"}' --dry-run
```

Без `-d` и без `--interactive` (нет терминала) команда требует данные —
подставьте адрес флагами, а остальное через `-d`:

```bash
python3 f2c_inventory.py equipment add --address 101 -d '{"name":"МФУ"}'
```

## Массовое добавление из CSV

```bash
python3 f2c_inventory.py import equipment --csv new_equipment.csv --mode create
```

Формат CSV — первая строка имена полей JSON:

```csv
name,serial_number,address_id,quantity,comment
Ноутбук HP 250,SN123,101,1,каб. 12
Проектор Epson,SN456,102,2,актовый зал
```

Сначала прогоните с `--dry-run`.

## Список и просмотр

```bash
python3 f2c_inventory.py equipment list                    # всё (1-я страница)
python3 f2c_inventory.py equipment list --address 101      # на адресе
python3 f2c_inventory.py equipment list --inventory 7      # в инвентаризации
python3 f2c_inventory.py equipment list --all --csv eq.csv # полная выгрузка
python3 f2c_inventory.py equipment show 500                # карточка
```

## Куда уходит запрос

По умолчанию создание идёт в `POST /api/equipment` (путь `/equipment` из
карты API с префиксом `/api`). Если сервер ждёт другой эндпоинт или поля —
посмотрите реальный запрос в DevTools (Network → XHR) и уточните:

```bash
python3 f2c_inventory.py equipment add --path /api/equipment/inventories/7/items \
    --method POST -d '{"name":"МФУ"}'
```
