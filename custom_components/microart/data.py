import logging
import async_timeout
from homeassistant.helpers.dispatcher import async_dispatcher_send

_LOGGER = logging.getLogger(__name__)

class MalinaDataFetcher:
    """Класс для опроса одного URL Малины с защитой от зависания."""
    def __init__(self, hass, session, url):
        self._hass = hass
        self._session = session
        self._url = url
        self.data = None
        self.available = False

    async def async_update(self):
        """Запрос данных с таймаутом в 10 секунд."""
        try:
            async with async_timeout.timeout(10):
                async with self._session.get(self._url) as response:
                    if response.status == 200:
                        raw = await response.json(content_type=None)
                        # Если Малина вернула массив [{}], берем первый элемент
                        self.data = raw if isinstance(raw, list) and len(raw) > 0 else raw
                        self.available = True
                        async_dispatcher_send(self._hass, f"microart_update_{id(self)}")
                    else:
                        self.available = False
        except Exception as err:
            _LOGGER.error("Ошибка связи с Малиной (%s): %s", self._url, err)
            self.available = False
