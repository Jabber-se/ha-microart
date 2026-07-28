import logging
from datetime import timedelta
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.core import callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

# Импортируем фетчер данных из работающего data.py
from .data import MalinaDataFetcher

_LOGGER = logging.getLogger(__name__)

DOMAIN = "microart"

URL_MAP = 'http://{}/read_json.php?device=map'
URL_BAT = 'http://{}/read_json.php?device=bat'
URL_MPPT = 'http://{}/read_json.php?device=mppt'

SENSOR_TYPES = {
    'grid_ac_power': ['inverter', '_PNET_calc', 'Grid Power', 'W', 'power', 'mdi:transmission-tower-export', 'power'],
    'grid_ac_current': ['inverter', '_INET_16_4', 'Grid Current', 'A', False, 'mdi:current-ac', 'current'],
    'grid_ac_voltage': ['inverter', '_UNET', 'Grid Voltage', 'V', False, 'mdi:current-ac', 'voltage'],
    'grid_ac_frequency': ['inverter', '_TFNET', 'Grid Frequency', 'Hz', False, 'mdi:sine-wave', 'frequency'],
    'load_ac_voltage': ['inverter', '_UOUTmed', 'Load Voltage', 'V', False, 'mdi:current-ac', 'voltage'],
    'load_ac_power': ['calculated', None, 'Load Power', 'W', 'power', 'mdi:home-lightning-bolt', 'power'],
    'load_ac_current': ['calculated', None, 'Load Current', 'A', False, 'mdi:current-ac', 'current'],
    'load_ac_frequency': ['inverter', '_ThFMAP', 'Load Frequency', 'Hz', False, 'mdi:sine-wave', 'frequency'],
    'dc_current': ['inverter', '_IAcc_med_A_u16', 'DC Current', 'A', False, 'mdi:current-dc', 'current'],
    'dc_voltage': ['inverter', '_Uacc', 'DC Voltage', 'V', False, 'mdi:current-dc', 'voltage'],
    'dc_power': ['inverter', '_PLoad_calc', 'DC Power', 'W', False, 'mdi:current-dc', 'power'],
    'map_bat_temp': ['inverter', '_Temp_Grad0', 'Battery Temperature', '°C', False, 'mdi:temperature-celsius', 'temperature'],
    'map_mode': ['inverter', '_MODE', 'Mode', '', False, 'mdi:solar-power', None],
    'map_charger_mode': ['inverter', '_Status_Char', 'Charger Mode', '', False, 'mdi:solar-power', None],
    'map_grid_energy': ['inverter', '_E_NET_SIGN', 'Grid Energy', 'kWh', 'energy', 'mdi:transmission-tower-import', 'energy'],
    'map_grid_sum_energy': ['inverter', '_E_NET', 'Grid Sum Energy', 'kWh', 'energy', 'mdi:transmission-tower', 'energy'],
    'map_grid_export_energy': ['calculated', None, 'Grid Export Energy', 'kWh', 'energy', 'mdi:transmission-tower-export', 'energy'],
    'map_discharge_energy': ['inverter', '_E_ACC', 'Inverter Discharge Energy', 'kWh', 'energy', 'mdi:battery-10', 'energy'],
    'map_charge_energy': ['inverter', '_E_ACC_CHARGE', 'Inverter Charge Energy', 'kWh', 'energy', 'mdi:battery-charging', 'energy'],
    'bat_c_ah_remain': ['battery', 'C_Ah_remain', 'Battery Remain Ah', 'Ah', False, 'mdi:battery', None],
    'bat_c_100_remain': ['battery', 'C_100_remain', 'SOC', '%', False, 'mdi:battery', 'battery'],
    'bat_ttg': ['battery', 'TTG', 'TTG', 'min', False, 'mdi:av-timer', 'duration'],
    'bat_current': ['battery', 'Iavg', 'Battery Current', 'A', False, 'mdi:current-dc', 'current'],
    'bat_voltage': ['battery', 'Uacc_avg', 'Battery Voltage', 'V', False, 'mdi:current-dc', 'voltage'],
    'bat_discharge_energy_day': ['battery', 'Esum_from_bat', 'Day Battery Discharge Energy', 'kWh', 'energy', 'mdi:battery-10', 'energy'],
    'bat_charge_energy_day': ['battery', 'Esum_to_bat', 'Day Battery Charge Energy', 'kWh', 'energy', 'mdi:battery-charging', 'energy'],
    'mppt_charge_energy_day': ['battery', 'mppt_day_E', 'Day MPPT Energy', 'kWh', 'energy', 'mdi:solar-panel', 'energy']
}

# Шаблон ТОЛЬКО для индивидуальных датчиков каждого конкретного MPPT (1-4)
MPPT_TEMPLATE = {
    'mppt_pv_power': ['mppt', 'P_PV', 'MPPT PV Power', 'W', 'power', 'mdi:solar-panel', 'power'],
    'mppt_pv_voltage': ['mppt', 'Vc_PV', 'MPPT PV Voltage', 'V', False, 'mdi:solar-panel', 'voltage'],
    'mppt_pv_current': ['mppt', 'Ic_PV', 'MPPT PV Current', 'A', False, 'mdi:solar-panel', 'current'],
    'mppt_power': ['mppt', 'P_Out', 'MPPT Power', 'W', 'power', 'mdi:solar-panel', 'power'],
    'mppt_voltage': ['mppt', 'V_Bat', 'MPPT Voltage', 'V', False, 'mdi:current-dc', 'voltage'],
    'mppt_current': ['mppt', 'I_Ch', 'MPPT Current', 'A', False, 'mdi:current-dc', 'current'],
    'mppt_relay1_status': ['mppt', 'R1', 'MPPT Relay1 Status', '', False, 'mdi:toggle-switch', None],
    'mppt_relay2_status': ['mppt', 'R2', 'MPPT Relay2 Status', '', False, 'mdi:toggle-switch', None],
    'mppt_relay3_status': ['mppt', 'R3', 'MPPT Relay3 Status', '', False, 'mdi:toggle-switch', None]
}

# Динамически размножаем индивидуальные датчики контроллеров
for i in range(1, 5):
    for key, val in MPPT_TEMPLATE.items():
        new_key = key.replace('mppt_', f'mppt{i}_')
        
        new_val = list(val)
        new_val[0] = f"mppt{i}"                       # тип устройства: 'mppt1', 'mppt2' ...
        new_val[2] = val[2].replace('MPPT ', f'MPPT {i} ') # Имя: 'MPPT 1 PV Power'
        
        SENSOR_TYPES[new_key] = new_val

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required('ip_address'): cv.string,
    vol.Optional('name', default='MAP'): cv.string,
    vol.Optional('units', default='kWh'): vol.In(['Wh', 'kWh']),
    vol.Optional('power_units', default='W'): vol.In(['W', 'kW']),
    vol.Optional('battery', default=False): cv.boolean,
    vol.Optional('mppt1', default=False): cv.boolean,
    vol.Optional('mppt2', default=False): cv.boolean,
    vol.Optional('mppt3', default=False): cv.boolean,
    vol.Optional('mppt4', default=False): cv.boolean,
    vol.Optional('scan_interval', default=timedelta(seconds=60)): cv.time_period,
})

# Наш новый главный метод инициализации для Config Flow вместо старого YAML
async def async_setup_entry(
    hass: HomeAssistant, 
    entry: ConfigEntry, 
    async_add_entities: AddEntitiesCallback
) -> None:
    """Настройка платформы датчиков на основе данных из глобального контекста."""
    # Достаем готовые данные из __init__.py
    entry_data = hass.data[DOMAIN][entry.entry_id]
    config = entry_data["config"]
    fetchers = entry_data["fetchers"]
    
    ip = config['ip_address']
    name = config.get('name', 'MAP')

    # === РЕГИСТРАЦИЯ ГЛАВНОГО ШЛЮЗА ПАК МАЛИНА В РЕЕСТРЕ ===
    from homeassistant.helpers import device_registry as dr
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"microart_gateway_{ip}")},
        name=f"ПАК Малина-2 ({ip})",
        manufacturer="МикроАрт",
        model="Программно-Аппаратный Комплекс",
        sw_version="Малина-2"
    )

    # Строго ваш легкий, быстрый и проверенный цикл создания сущностей с индексами
    sensors = []
    for var, cfg in SENSOR_TYPES.items():
        device_type = cfg[0]  # Из индекса 0 берём тип устройства
        
        # 1. Если это вычисляемый сенсор нагрузки
        if device_type == 'calculated':
            unit = cfg[3]
            sensors.append(MicroArtSensor(fetchers['inverter'], name, var, unit, ip))
            continue
            
        # 2. Если это индивидуальный датчик MPPT (mppt1, mppt2, mppt3, mppt4)
        if device_type.startswith('mppt'):
            if not config.get(device_type, False):
                continue
            sensors.append(MicroArtSensor(fetchers['mppt'], name, var, cfg[3], ip))
            continue
            
        # 3. Для обычных физических датчиков (inverter, battery)
        if device_type not in fetchers:
            continue

        unit = cfg[3]
        convert_units = cfg[4]
        
        # Задаем дефолтные значения для юнитов, так как в UI пока нет этих настроек
        if convert_units == 'power':
            unit = config.get('power_units', 'W')
        elif convert_units == 'energy':
            unit = config.get('units', 'kWh')
            
        # Ваш жесткий фикс для текстовых статусов режима и зарядки
        if var in ('map_mode', 'map_charger_mode'):
            unit = None

        sensors.append(MicroArtSensor(fetchers[device_type], name, var, unit, ip))
        
        hass.data[DOMAIN][entry.entry_id]["fetchers"] = fetchers

    async_add_entities(sensors, True)

class MicroArtSensor(SensorEntity):
    """Сенсор Home Assistant, полностью восстановленный до рабочей версии."""
    def __init__(self, fetcher, name, variable, unit, ip):
        cfg = SENSOR_TYPES[variable]
        self._fetcher = fetcher
        self._variable = variable
        self._json_key = cfg[1]
        
        self._attr_name = f"{name} {cfg[2]}"
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = cfg[5]
        
        if cfg[6] is not None:
            self._attr_device_class = cfg[6]
            
        self._attr_unique_id = f"malina_{ip}_{variable}"
        
        self._ip = ip
        self._name = name
        
        if variable in (
            'grid_ac_frequency',
            'load_ac_frequency',
            'grid_ac_current',
            'load_ac_current',
            'dc_voltage',
            'bat_voltage',
            'mppt1_pv_voltage',
            'mppt1_voltage',
            'mppt2_pv_voltage',
            'mppt2_voltage',
            'mppt3_pv_voltage',
            'mppt3_voltage',
            'mppt4_pv_voltage',
            'mppt4_voltage',
        ):
            self._attr_suggested_display_precision = 1

    @property
    def available(self) -> bool:
        return self._fetcher.available

    @property
    def device_info(self):
        """Связывает сенсор с конкретным виртуальным устройством (карточкой в UI)."""
        device_type = SENSOR_TYPES[self._variable][0] # 'inverter', 'battery', 'mppt1'...'mppt4' or 'calculated'
        
        # 1. Определяем, к какой группе (карточке) относится этот сенсор
        if device_type in ('inverter', 'calculated'):
            device_id = f"microart_map_{self._ip}"
            device_name = f"{self._name} Инвертор МАП"
            model_name = "МАП Энергия"
        elif device_type == 'battery':
            device_id = f"microart_bat_{self._ip}"
            device_name = f"{self._name} Монитор АКБ"
            model_name = "Батарейный Монитор"
        elif device_type.startswith('mppt'):
            # Вытаскиваем номер контроллера для раздельных карточек (MPPT 1, MPPT 2...)
            mppt_num = device_type.replace('mppt', '')
            device_id = f"microart_mppt{mppt_num}_{self._ip}"
            device_name = f"{self._name} Контроллер MPPT {mppt_num}"
            model_name = "КЭС MPPT PRO"
        else:
            return None

        # Возвращаем структуру для Device Registry
        return {
            "identifiers": {(DOMAIN, device_id)}, # Уникальный ID карточки устройства
            "name": device_name,                  # Красивое имя карточки на экране
            "manufacturer": "МикроАрт",           # Производитель железа
            "model": model_name,                  # Модель устройства
            "via_device": (DOMAIN, f"microart_gateway_{self._ip}"), # Указываем, что они работают через шлюз Малины
        }

    @property
    def native_value(self):
        """Прямое извлечение данных с жесткой и безопасной математикой конвертации."""
        
        # === БЛОК ВЫЧИСЛЯЕМЫХ СЕНСОРОВ НАГРУЗКИ (БЕЗ MPPT) ===
        if SENSOR_TYPES[self._variable][0] == 'calculated':
            if not self._fetcher.available or self._fetcher.data is None:
                return None

            # Хелпер для быстрого и безопасного извлечения данных из инвертора
            def get_inverter_val(var_name):
                key = SENSOR_TYPES[var_name][1]
                val = self._fetcher.data.get(key)
                try:
                    return float(val) if val is not None else None
                except (ValueError, TypeError):
                    return None

            # 1. Расчет мощности нагрузки: load_ac_power = grid_ac_power - dc_power
            if self._variable == 'load_ac_power':
                grid_p = get_inverter_val('grid_ac_power')
                dc_p = get_inverter_val('dc_power')
                
                if grid_p is not None and dc_p is not None:
                    return int(round(grid_p - dc_p, 0))
                return None

            # 2. Расчет тока нагрузки: load_ac_current = load_ac_power / load_ac_voltage
            elif self._variable == 'load_ac_current':
                load_v = get_inverter_val('load_ac_voltage')
                grid_p = get_inverter_val('grid_ac_power')
                dc_p = get_inverter_val('dc_power')

                if grid_p is not None and dc_p is not None and load_v and load_v > 0:
                    load_p = grid_p - dc_p
                    return float(round(load_p / load_v, 2))
                return None

            # 3. РАСЧЕТ ЭКСПОРТА ЭНЕРГИИ В СЕТЬ (Суммарная - Потребленная) / 100
            elif self._variable == 'map_grid_export_energy':
                grid_i = get_inverter_val('map_grid_energy')
                grid_s = get_inverter_val('map_grid_sum_energy')
                
                if grid_i is not None and grid_s is not None:
                    return round((float(grid_s) - float(grid_i)) / 100.0, 2)
                return None

        if not self._fetcher.available or self._fetcher.data is None:
            return None
            
        # Корректно забираем сырое значение по ключу (работает и для {} и для [{}])
        if isinstance(self._fetcher.data, list):
            device_type = SENSOR_TYPES[self._variable][0] # Узнаем тип устройства: 'mppt1', 'mppt2' и т.д.
            
            # Определяем нужный индекс в массиве Малины
            if device_type.startswith('mppt'):
                try:
                    # Извлекаем номер из 'mppt2' -> 2. Вычитаем 1, чтобы получить индекс 1 в Python
                    idx = int(device_type.replace('mppt', '')) - 1
                except (ValueError, TypeError):
                    idx = 0
            else:
                idx = 0 # Для инвертора, батареи и прочего всегда берем первый элемент

            # Проверяем, что Малина реально прислала этот контроллер в массиве
            if len(self._fetcher.data) > idx:
                state = self._fetcher.data[idx].get(self._json_key)
            else:
                return None
        else:
            state = self._fetcher.data.get(self._json_key)

        if state is None:
            return None

        current_unit = self.native_unit_of_measurement

        # Применяем оригинальную математику по точным именам сенсоров из вашей конфигурации
        # 1. Сенсоры ЭНЕРГИИ инвертора (делим на 100)
        if self._variable in ('map_discharge_energy', 'map_charge_energy', 'map_grid_energy', 'map_grid_sum_energy'):
            if current_unit == "kWh":
                try:
                    return round(float(state) / 100.0, 2)
                except (ValueError, TypeError):
                    pass

        # 2. Сенсоры ЭНЕРГИИ батареи и MPPT (делим на 1000)
        elif self._variable in ('bat_discharge_energy_day', 'bat_charge_energy_day', 'mppt_charge_energy_day'):
            if current_unit == "kWh":
                try:
                    return round(float(state) / 1000.0, 2)
                except (ValueError, TypeError):
                    pass

        # 3. Конвертация МОЩНОСТИ в кВт (kW), если выбрано в YAML
        elif self._variable in ('grid_ac_power', 'dc_power', 'mppt_pv_power', 'mppt_power'):
            if current_unit == "kW":
                try:
                    return round(float(state) / 1000.0, 2)
                except (ValueError, TypeError):
                    pass

        # 4. ДЛЯ ВСЕХ ОСТАЛЬНЫХ СЕНСОРОВ
        # Если это статус реле, возвращаем чистое целое число 1 или 0
        if "relay" in self._variable:
            try:
                val = int(float(state))
                return 1 if val == 1 else 0
            except (ValueError, TypeError):
                return 0

        # ПЕРЕВОД СТАТУСА МАП (_MODE) В ТЕКСТ
        if self._variable == "map_mode":
            mode_map = {
                0: "OFF, No grid",
                1: "OFF, Grid connected",
                2: "ON, Inverting, no grid",
                3: "ON, Grid pass-through",
                4: "ON, Grid pass-through & Battery charging",
                10: "Forced Inverting",
                11: "Peak tariff, Forced Inverting",
                12: "Off-peak tariff",
                13: "Grid pass-through & Solar assist",
                14: "Grid pass-through & Feed-in",
                15: "Standby, Awaiting charge",
                16: "Peak tariff, Pass-through & Solar assist",
                17: "Peak tariff, Pass-through & Feed-in",
                18: "PowerAssist Pmax"
            }
            try:
                # Малина присылает код строкой, переводим в int для поиска по словарю
                code = int(float(state))
                return mode_map.get(code, f"Unknown Mode ({code})")
            except (ValueError, TypeError):
                return f"Error code ({state})"

        # ПЕРЕВОД СТАТУСА ЗАРЯДНОГО УСТРОЙСТВА (_Status_Char) В ТЕКСТ
        if self._variable == "map_charger_mode":
            charger_map = {
                0: "No charging",
                6: "Stage 1 charging required",
                14: "Stage 2 charging",
                15: "Float charging"
            }
            try:
                code = int(float(state))
                return charger_map.get(code, f"Unknown Stage ({code})")
            except (ValueError, TypeError):
                return f"Error code ({state})"

        # Для всех остальных стандартных числовых значений (Вольты, Амперы, Герцы)
        try:
            return float(state)
        except (ValueError, TypeError):
            return state



