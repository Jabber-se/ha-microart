import logging
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
# Импортируем ваш класс фетчера из файла data.py
from .data import MalinaDataFetcher

DOMAIN = "microart"
PLATFORMS = ["sensor", "switch"]

URL_MAP = 'http://{}/read_json.php?device=map'
URL_BAT = 'http://{}/read_json.php?device=bat'
URL_MPPT = 'http://{}/read_json.php?device=mppt'

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка интеграции MicroArt при загрузке из интерфейса (UI)."""
    config = entry.data
    ip = config['ip_address']
    
    session = async_get_clientsession(hass)
    scan_interval = timedelta(seconds=int(config.get('scan_interval', 60)))

    # Инициализируем сетевые фетчеры, передавая hass, session и url
    fetchers = {}
    fetchers['inverter'] = MalinaDataFetcher(hass, session, URL_MAP.format(ip))
    
    if config.get('battery'):
        fetchers['battery'] = MalinaDataFetcher(hass, session, URL_BAT.format(ip))
        
    if config.get('mppt1') or config.get('mppt2') or config.get('mppt3') or config.get('mppt4'):
        fetchers['mppt'] = MalinaDataFetcher(hass, session, URL_MPPT.format(ip))

    # Запускаем независимые таймеры опроса сети
    for fetcher in fetchers.values():
        await fetcher.async_update()
        
        @callback
        def fire_update(*_, f=fetcher):
            hass.async_create_task(f.async_update())
            
        async_track_time_interval(hass, fire_update, scan_interval)

    # Сохраняем и конфиг, и фетчеры ДО загрузки платформ!
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "config": config,
        "fetchers": fetchers
    }

    # Теперь безопасно запускаем сенсоры и свитчи
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Перезапускает интеграцию при изменении настроек в UI."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Удаление интеграции пользователем."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
