import logging
import aiohttp
import async_timeout
import voluptuous as vol
import re

from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

DOMAIN = "microart"
_LOGGER = logging.getLogger(__name__)

async def scan_malina_devices(session: aiohttp.ClientSession, ip: str) -> dict:
    """Опрашивает devices.php и вытаскивает количество подключенных устройств."""
    url = f"http://{ip}/devices.php"
    result = {"battery": False, "mppt_count": 0}
    
    try:
        async with async_timeout.timeout(5):
            async with session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    _LOGGER.debug("Ответ от devices.php: %s", text)
                    
                    # 1. Парсим количество MPPT (ищем MPPT:Х)
                    mppt_match = re.search(r"MPPT:(\d+)", text)
                    if mppt_match:
                        result["mppt_count"] = int(mppt_match.group(1))
                        
                    # 2. Парсим инвертор и батарейный монитор (ищем MAC:X)
                    # Если MAC:1 — автоматически взводим галочку батарейного монитора bat
                    mac_match = re.search(r"MAC:(\d+)", text)
                    if mac_match and int(mac_match.group(1)) == 1:
                        result["battery"] = True
                        
                    return result
    except Exception as err:
        _LOGGER.warning("Не удалось отсканировать устройства на Малине %s: %s", url, err)
        
    return None # Возвращаем None, если сервер вообще недоступен


class MicroArtConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Двухэтапный визард первоначальной настройки с автосканированием."""
    
    VERSION = 1

    def __init__(self):
        """Инициализация временного хранилища данных между шагами."""
        self.discovered_info = {}
        self.user_data = {}

    async def async_step_user(self, user_input=None):
        """ЭТАП 1: Запрос IP-адреса и префикса."""
        errors = {}

        if user_input is not None:
            ip = user_input["ip_address"]
            
            # Защита от дубликатов по IP
            await self.async_set_unique_id(f"microart_{ip}")
            self._abort_if_unique_id_configured()
            
            session = async_get_clientsession(self.hass)
            # Стучимся на devices.php
            scan_result = await scan_malina_devices(session, ip)
            
            if scan_result is not None:
                # Связь есть! Сохраняем данные первого шага
                self.user_data = user_input
                self.discovered_info = scan_result
                # Переходим к ЭТАПУ 2
                return await self.async_step_devices()
            else:
                errors["base"] = "cannot_connect"

        data_schema = vol.Schema({
            vol.Required("ip_address"): str,
            vol.Optional("name", default="MAP"): str,
        })

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_devices(self, user_input=None):
        """ЭТАП 2: Вывод предзаполненных чекбоксов и интервала опроса."""
        if user_input is not None:
            # Объединяем данные первого и второго шага в один финальный конфиг
            final_data = {**self.user_data, **user_input}
            return self.async_create_entry(
                title=f"Малина ({final_data['ip_address']})",
                data=final_data
            )

        # Вытаскиваем то, что отсканировали на первом шаге
        mppt_count = self.discovered_info.get("mppt_count", 0)
        auto_battery = self.discovered_info.get("battery", False)

        interval_options = {30: "30 секунд", 60: "60 секунд (По умолчанию)", 90: "90 секунд", 120: "120 секунд"}

        # Автоматически ставим True для чекбоксов на основе сканирования
        data_schema = vol.Schema({
            vol.Optional("scan_interval", default=60): vol.In(interval_options),
            vol.Optional("battery", default=auto_battery): cv.boolean,
            vol.Optional("mppt1", default=bool(mppt_count >= 1)): cv.boolean,
            vol.Optional("mppt2", default=bool(mppt_count >= 2)): cv.boolean,
            vol.Optional("mppt3", default=bool(mppt_count >= 3)): cv.boolean,
            vol.Optional("mppt4", default=bool(mppt_count >= 4)): cv.boolean,
        })

        return self.async_show_form(step_id="devices", data_schema=data_schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Обработчик повторного запуска визарда по кнопке 'Настроить'."""
        return MicroArtOptionsFlowHandler()

class MicroArtOptionsFlowHandler(config_entries.OptionsFlow):
    """Повторный запуск визарда для изменения периода или галочек на лету."""

    # ВНИМАНИЕ: Метод __init__ удален, так как базовый класс его не имеет в новых версиях HA

    async def async_step_init(self, user_input=None):
        """Позволяет изменять период опроса и галочки без удаления интеграции."""
        if user_input is not None:
            # Обновляем базовые данные записи напрямую через встроенное свойство config_entry
            new_data = {**self.config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            return self.async_create_entry(title="", data={})

        # Читаем конфигурацию через официальное свойство self.config_entry
        current_config = self.config_entry.data or {}
        interval_options = {30: "30 секунд", 60: "60 секунд", 90: "90 секунд", 120: "120 секунд"}

        raw_interval = current_config.get("scan_interval", 60)
        try:
            default_interval = int(raw_interval)
            if default_interval not in interval_options:
                default_interval = 60
        except (ValueError, TypeError):
            default_interval = 60

        options_schema = vol.Schema({
            vol.Optional("scan_interval", default=default_interval): vol.In(interval_options),
            vol.Optional("battery", default=bool(current_config.get("battery", False))): cv.boolean,
            vol.Optional("mppt1", default=bool(current_config.get("mppt1", False))): cv.boolean,
            vol.Optional("mppt2", default=bool(current_config.get("mppt2", False))): cv.boolean,
            vol.Optional("mppt3", default=bool(current_config.get("mppt3", False))): cv.boolean,
            vol.Optional("mppt4", default=bool(current_config.get("mppt4", False))): cv.boolean,
        })

        return self.async_show_form(step_id="init", data_schema=options_schema)
