import logging
import asyncio
import async_timeout
import voluptuous as vol

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

DOMAIN = "microart"

# URL для отправки команд управления реле
URL_WRITE = 'http://{}/write_sec.php?id={}&relay={}&mode={}'

# Шаблон настроек для реле внутри КЭС: JSON-ключ, Порядковый номер реле
SWITCH_TEMPLATE = {
    'mppt_relay1': ['R1', 1],
    'mppt_relay2': ['R2', 2],
    'mppt_relay3': ['R3', 3],
}

async def async_setup_entry(
    hass: HomeAssistant, 
    entry: ConfigEntry, 
    async_add_entities: AddEntitiesCallback
) -> None:
    """Настройка платформы выключателей на основе данных из глобального контекста."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    config = entry_data["config"]
    fetchers = entry_data["fetchers"]
    
    session = async_get_clientsession(hass)
    ip = config['ip_address']
    name = config.get('name', 'MAP')
    mppt_fetcher = fetchers.get("mppt")

    switches = []
    
    # Цикл по всем 4-м возможным MPPT-контроллерам
    for i in range(1, 5):
        if config.get(f'mppt{i}'):
            for key, val in SWITCH_TEMPLATE.items():
                var_name = key.replace('mppt_', f'mppt{i}_')
                json_key = val[0]
                relay_num = val[1]
                
                switches.append(
                    MicroArtRelaySwitch(
                        hass, mppt_fetcher, session, name, var_name, i, json_key, relay_num, ip
                    )
                )

    async_add_entities(switches, True)


class MicroArtRelaySwitch(SwitchEntity):
    """Выключатель реле КЭС MPPT с оптимистичным удержанием состояния и таймером блокировки кэша."""

    def __init__(self, hass, fetcher, session, name, variable, mppt_num, json_key, relay_num, ip):
        self.hass = hass
        self._fetcher = fetcher
        self._session = session
        self._ip = ip
        self._variable = variable
        self._mppt_num = mppt_num
        self._json_key = json_key
        self._relay_num = relay_num
        self._name = name
        
        self._attr_name = f"Relay {relay_num}"
        self._attr_icon = "mdi:toggle-switch"
        self._attr_unique_id = f"malina_{ip}_{variable}_switch"

        # ПЕРЕМЕННЫЕ ТАЙМЕРА БЛОКИРОВКИ:
        self._optimistic_on = False       # Виртуальное состояние свитча при клике
        self._lock_feedback = False       # Флаг: активна ли блокировка обратной связи сети
        self._timer_task = None           # Ссылка на асинхронную задачу таймера

    async def async_added_to_hass(self) -> None:
        """Регистрируем подписку на сигналы обновления фетчера."""
        from homeassistant.core import callback

        @callback
        def _update_callback():
            # Заставляет Home Assistant мгновенно пересчитать свойство is_on и перерисовать тумблер в Lovelace
            self.async_write_ha_state()

        # Подписываемся на уникальное событие именно НАШЕГО фетчера MPPT
        if self._fetcher:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, f"microart_update_{id(self._fetcher)}", _update_callback
                )
            )

    @property
    def is_on(self) -> bool:
        """Возвращает состояние свитча с учетом таймера блокировки обратной связи."""
        # 1. Если таймер активен — намертво держим на экране оптимистичное состояние из переменной
        if self._lock_feedback:
            return self._optimistic_on

        # 2. Если таймер истек — читаем чистую правду из JSON Малины
        if self._fetcher is None or not self._fetcher.available or self._fetcher.data is None:
            return False
            
        try:
            idx = self._mppt_num - 1
            if isinstance(self._fetcher.data, list) and len(self._fetcher.data) > idx:
                state = self._fetcher.data[idx].get(self._json_key)
                if state is not None:
                    return int(float(state)) == 1
        except (ValueError, TypeError, IndexError):
            pass
            
        return False

    @property
    def available(self) -> bool:
        """Если фетчер недоступен, тумблер гаснет автоматически."""
        if self._fetcher is None:
            return False
        return self._fetcher.available

    @property
    def device_info(self):
        """Связываем выключатель реле строго с нужной карточкой КЭС MPPT PRO!"""
        return {
            "identifiers": {(DOMAIN, f"microart_mppt{self._mppt_num}_{self._ip}")},
            "name": f"{self._name} Контроллер MPPT {self._mppt_num}",
            "manufacturer": "МикроАрт",
            "model": "КЭС MPPT PRO",
            "via_device": (DOMAIN, f"microart_gateway_{self._ip}"),
        }

    async def async_turn_on(self, **kwargs):
        """Включение реле через HTTP-запрос (mode=1)."""
        url = URL_WRITE.format(self._ip, self._mppt_num, self._relay_num, "1")
        
        # Включаем оптимистичный режим ДО отправки, чтобы интерфейс среагировал мгновенно
        self._start_feedback_lock(True)
        
        if not await self._send_command(url):
            # Если Малина ответила ошибкой — сразу сбрасываем замок обратной связи
            self._stop_feedback_lock()

    async def async_turn_off(self, **kwargs):
        """Выключение реле через HTTP-запрос (mode=0)."""
        url = URL_WRITE.format(self._ip, self._mppt_num, self._relay_num, "0")
        
        self._start_feedback_lock(False)
        
        if not await self._send_command(url):
            self._stop_feedback_lock()

    def _start_feedback_lock(self, target_state: bool):
        """Включает замок удержания состояния в UI и запускает 10-секундный таймер."""
        # Если прошлый таймер еще тикал — отменяем его
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()

        self._optimistic_on = target_state
        self._lock_feedback = True
        self.async_write_ha_state() # Мгновенно перерисовываем тумблер на экране в нужное положение

        # Запускаем асинхронный таймер на 10 секунд
        self._timer_task = self.hass.async_create_task(self._async_lock_timer(5))

    def _stop_feedback_lock(self):
        """Принудительно снимает блокировку обратной связи."""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._lock_feedback = False
        self.async_write_ha_state()

    async def _async_lock_timer(self, delay: int):
        """Асинно спит delay секунд, после чего возвращает управление сети."""
        try:
            await asyncio.sleep(delay)
            self._lock_feedback = False
            _LOGGER.debug("Таймер удержания реле истек, возвращаемся к чтению JSON")
            
            # По истечении 10 секунд принудительно обновляем фетчер сети, 
            # так как Малина уже гарантированно успела записать новые статусы в JSON
            if self._fetcher:
                self.hass.async_create_task(self._fetcher.async_update())
                
            self.async_write_ha_state()
        except asyncio.CancelledError:
            pass

    async def _send_command(self, url) -> bool:
        """Отправка HTTP-команды на Малину с проверкой 'ok'."""
        try:
            async with async_timeout.timeout(5):
                async with self._session.get(url) as response:
                    if response.status == 200:
                        text = await response.text()
                        if "ok" in text.lower():
                            _LOGGER.debug("Команда успешно выполнена Малиной: %s", url)
                            return True
                        _LOGGER.warning("Малина вернула не 'OK': %s (URL: %s)", text, url)
                    else:
                        _LOGGER.error("Ошибка команды, статус HTTP: %s", response.status)
        except Exception as err:
            _LOGGER.error("Ошибка связи при отправке команды управления реле: %s", err)
        return False

