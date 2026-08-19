# Все операции с карточкой оборудования (`card`)

Карточка — запись оборудования со всеми её данными, фото, статусом и
историей. Команда `card` покрывает полный жизненный цикл карточки.

## Интерактивное меню

```bash
python3 f2c_inventory.py card 500
```

Открывает меню действий по карточке:

```
▶ Действие с карточкой
 [1] Показать карточку
 [2] Редактировать поля
 [3] Перенести на другой адрес
 [4] Отметить «найдено»
 [5] Отметить «не найдено»
 [6] Осмотр (review)
 [7] Фотографии
 [8] Добавить фото
 [9] Поиск по серийному номеру
[10] Журнал изменений
[11] Удалить карточку
[12] Выход
```

## Отдельные действия

| Действие | Команда | Что делает |
|---|---|---|
| Показать | `card 500 show` | полная карточка (JSON) |
| Редактировать | `card 500 edit -d status=repair` | PUT/PATCH полей карточки |
| Перенос | `card 500 transfer --to 102` | перенос на другой адрес (движение) |
| Найдено | `card 500 found --inventory 7` | отметка по итогам инвентаризации |
| Не найдено | `card 500 unfound --inventory 7` | отметка «не найдено» |
| Осмотр | `card 500 review --comment "в норме"` | результаты осмотра |
| Фото | `card 500 photos` | список фотографий |
| Добавить фото | `card 500 photo-add --file photo.jpg` | загрузка фото (multipart) |
| По серийнику | `card 500 serial --number SN123` | найти оборудование по серийному номеру |
| Фото серийника | `card 500 serial-photo --file tag.jpg` | распознавание серийника по фото |
| Журнал | `card 500 history` | история изменений карточки |
| Удалить | `card 500 delete --yes` | удаление (спросит подтверждение) |

## Подробнее о ключевых операциях

### Перенос (transfer)

`card 500 transfer` без `--to` открывает интерактивный выбор
адреса-получателя (организация → адрес с фильтром). Тело по умолчанию
`{"address_id": <ID>}`; если сервер ждёт другое поле — задайте тело явно:

```bash
python3 f2c_inventory.py card 500 transfer --to 102 \
    -d '{"to_address_id": 102, "reason": "переезд"}'
```

### Найдено / не найдено (found/unfound)

Эти отметки в интерфейсе выполняются **внутри инвентаризации**, поэтому
маршрут — `/equipment/inventories/{inv}/{eq}/found` (и `/unfound`).
Передавайте `--inventory ID` (список инвентаризаций: `list inventories`).
Без него будет использован простой путь `/api/equipment/{id}/found`.

```bash
python3 f2c_inventory.py card 500 found --inventory 7 --dry-run
python3 f2c_inventory.py card 500 found --inventory 7
```

### Фотографии

```bash
python3 f2c_inventory.py card 500 photos                          # список
python3 f2c_inventory.py card 500 photo-add --file ./photo.jpg    # загрузка
python3 f2c_inventory.py card 500 photo-add --file p.jpg --field image  # имя поля
```

### Поиск по серийному номеру

```bash
# вручную: POST /api/equipment/serial-lookup/manual {"serial": "..."}
python3 f2c_inventory.py card 500 serial --number SN123

# распознавание по фото: POST /api/equipment/serial-lookup/photo (multipart)
python3 f2c_inventory.py card 500 serial-photo --file ./tag.jpg
```

Тело для `serial` по умолчанию `{"serial": "..."}`; если сервер ждёт
`serial_number` — `card 500 serial --number SN123 -d '{"serial_number":"SN123"}'`.

## Скрипты и dry-run

Все мутирующие действия поддерживают `--dry-run` (показ запроса без
отправки) и `--yes` (без подтверждений — для cron):

```bash
python3 f2c_inventory.py card 500 unfound --inventory 7 --yes
```

## Как уточнить эндпоинты

Карта API из бандла не содержит методов, поэтому команда пробует кандидатов
по очереди (шаблоны из карты → типовые REST-пути). Первый отвечающий —
используется. Если ни один не подошёл, поможет HAR:

```bash
python3 f2c_inventory.py recon --har chrome-net-export-log.json
```

или точечное указание `--path`/`--method`. Список реальных маршрутов из
вашей карты API:

```
/equipment/inventories/{inv}/{eq}/found|unfound|transfer|review|photos/{n}
/equipment/serial-lookup/manual            (поиск по серийному номеру)
/equipment/serial-lookup/photo             (распознавание по фото)
/inventories/{id}/start|complete|reopen|paid|transition
/inventories/{id}/curator|administrator|opergroup|assign-self-curator
/organizations/{org}/addresses/{addr}/move (перенос адреса)
/journals/meta                             (журналы учёта)
```
