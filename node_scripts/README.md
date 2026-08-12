# Remnawave Node: ручная проверка установки

Эти скрипты предназначены для первого, ручного этапа проверки перед добавлением
установки нод в Telegram-админку.

## Что понадобится

- новый VPS с Ubuntu/Debian;
- root-доступ или пользователь с `sudo`;
- доступ к VPS по SSH;
- `docker-compose.yml`, сгенерированный в Remnawave для новой ноды;
- Windows OpenSSH (`ssh.exe` и `scp.exe`) на компьютере, с которого запускается
  `deploy-node.ps1`.

Пароль SSH нигде не сохраняется и не передаётся аргументом командной строки.
Его запрашивает стандартный клиент OpenSSH. Лучше использовать SSH-ключ.

## Создание ноды и compose-файла

Скрипт по умолчанию читает `REMNAWAVE_BASE_URL`, `REMNAWAVE_API_TOKEN` и
настройки nginx-cookie из корневого `.env` проекта. Токен в вывод не попадает.

Из корня проекта запустите:

```powershell
python .\node_scripts\create-node.py
```

Скрипт запросит название и публичный IP/hostname ноды, покажет доступные config
profiles, создаст ноду через API и сохранит файлы сюда:

```text
node_scripts\generated\<имя-ноды>-<uuid>\docker-compose.yml
node_scripts\generated\<имя-ноды>-<uuid>\node.json
```

Каталог `generated` исключён из Git, потому что compose содержит секрет ноды.

Для полностью параметризованного запуска:

```powershell
python .\node_scripts\create-node.py `
  --name node-nl-1 `
  --address 203.0.113.10 `
  --node-port 2222 `
  --country-code NL
```

`--node-port` — внутренний API-порт Remnawave Node, а не SSH-порт.

## Установка на VPS

После успешного выполнения `create-node.py` передайте созданный compose в
установщик:

```powershell
.\node_scripts\deploy-node.ps1 `
  -HostAddress 203.0.113.10 `
  -Port 22 `
  -User root `
  -ComposeFile .\node_scripts\generated\node-nl-1-ab12cd34\docker-compose.yml
```

При первом соединении внимательно проверьте SSH fingerprint сервера. Скрипт
скопирует файлы во временный каталог, а установщик разместит рабочую
конфигурацию в `/opt/remnanode/docker-compose.yml`.

Можно указать SSH-ключ:

```powershell
.\node_scripts\deploy-node.ps1 -HostAddress 203.0.113.10 -User root `
  -IdentityFile C:\Users\me\.ssh\id_ed25519 `
  -ComposeFile C:\temp\docker-compose.yml
```

После установки проверьте в панели, что нода перешла в online.

## Проверка и логи на VPS

```bash
sudo /opt/remnanode/node-status.sh
cd /opt/remnanode && sudo docker compose logs --tail=100
```

## Удаление контейнера с VPS

Удаление требует ввода точной фразы `DELETE REMNANODE`:

```bash
sudo /opt/remnanode/uninstall-node.sh
```

По умолчанию конфигурация и данные остаются в `/opt/remnanode`. Для удаления
рабочего каталога после остановки контейнера:

```bash
sudo /opt/remnanode/uninstall-node.sh --purge
```

Удаление VPS-контейнера не удаляет запись ноды из Remnawave. На этом этапе её
нужно удалить отдельно в панели.

## Ограничения первого этапа

- поддерживаются Ubuntu и Debian;
- API-сценарий рассчитан на Remnawave 2.7/2.8 с endpoint `/api/keygen`;
- автоматизация админки будет использовать тот же сценарий, но передавать compose
  по SSH без ручного запуска PowerShell.
