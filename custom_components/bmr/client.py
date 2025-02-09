# Author: Honza Slesinger, Adam Chylek
# Tested with:
#    BMR HC64 v2013

import logging
import re
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Any, Dict, List, Optional, TypedDict

import backoff
from aiohttp import ClientSession, FormData
from asyncache import cached
from cachetools import LRUCache, TTLCache
from homeassistant.helpers.storage import Store

from .const import TIMEOUT

_LOGGER = logging.getLogger(__name__)


CACHE_DEFAULT_MAXSIZE = 128
CACHE_DEFAULT_TTL = 10
BACKOFF_TRIES = 5
DEFAULT_HEADERS = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
DEFAULT_REQ_DATA = {"param": "+"}


class AuthException(Exception):
    pass


TEMPERATURE_OVERRIDE_CHECK_DELAY = 300  # seconds, how much time to wait before checking for new temperature overrides


class TemperatureOverride:
    def __init__(self,
                 temperature: float, created_at: datetime,
                 stop_at: Optional[datetime], last_set: Optional[datetime] = None,
                 disabled_at: Optional[datetime] = None):
        self.created_at = created_at
        self.last_set = last_set or created_at
        self.temperature = temperature
        self.stop_at = stop_at
        self.disabled_at = disabled_at

    def serialize(self) -> Dict[str, Any]:
        # convert datetime to seconds from epoch and return as dict
        return {
            "temperature": self.temperature,
            "created_at": self.created_at.timestamp(),
            "stop_at": self.stop_at.timestamp() if self.stop_at else None,
            "disabled_at": self.disabled_at.timestamp() if self.disabled_at else None,
            "last_set": self.last_set.timestamp(),
        }

    @classmethod
    def deserialize(cls, data: Dict[str, Any]) -> "TemperatureOverride":
        # check if created_at is string (meaning a legacy format is used)
        # and convert all times to datetime
        if isinstance(data["created_at"], str):
            return cls(
                temperature=data["temperature"],
                created_at=datetime.fromisoformat(data["created_at"]),
                stop_at=datetime.fromisoformat(data["stop_at"]) if data.get("stop_at") else None,
                disabled_at=datetime.fromisoformat(data["disabled_at"]) if data.get("disabled_at") else None,
                # if last_set is not available, use created_at
                last_set=datetime.fromisoformat(data["last_set"] if data.get("last_set") else data["created_at"]),
            )

        # convert timestamp seconds to datetime and return new instance
        return cls(
            temperature=data["temperature"],
            created_at=datetime.fromtimestamp(data["created_at"]),
            stop_at=datetime.fromtimestamp(data["stop_at"]) if data.get("stop_at") else None,
            disabled_at=datetime.fromtimestamp(data["disabled_at"]) if data.get("disabled_at") else None,
            # if last_set is not available, use created_at
            last_set=datetime.fromtimestamp(data["last_set"] if data.get("last_set") else data["created_at"]),
        )

    def __repr__(self):
        return str(self.__dict__)


class Bmr:
    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        can_cool: bool,
        session: ClientSession,
        overrides: Optional[Dict[int, Dict[str, Any]]] = None,
        overrides_store: Optional[Store[Any]] = None,
    ):
        self._user = user
        self._password = password
        self.can_cool = can_cool
        self.overrides: Dict[int, TemperatureOverride] = {}
        if overrides is not None:
            for key, value in overrides.items():
                try:
                    self.overrides[int(key)] = TemperatureOverride.deserialize(value)
                except Exception:
                    # skip the override if it's malformed
                    _LOGGER.exception(f"Error while loading override for circuit {key}")
        self.overrides_store = overrides_store
        self.session = session
        self.base_url = base_url

    @backoff.on_exception(backoff.expo, Exception, max_tries=BACKOFF_TRIES)
    async def _post(self,
                    url: str,
                    data: Dict[str, Any] = DEFAULT_REQ_DATA,
                    headers: Optional[Dict[str, str]] = DEFAULT_HEADERS
                    ) -> str:
        """Send a POST request to the BMR controller.

        Tries to send a POST request to the BMR controller. If the request
        fails, it will try to re-authenticate and then re-send the request.

        Args:
            url: URL to send the request to.
            data: Data to send in the request.
            headers: Headers to send in the request.

        Returns:
            Response text from the server.
        """
        try:
            async with self.session.post(f"{self.base_url}/{url}",
                                         data=FormData(data),
                                         headers=headers,
                                         timeout=TIMEOUT) as response:
                resp = await response.text()
                if resp.strip() == "" or resp.strip() == "\0":
                    raise Exception("Empty response - need to re-authenticate")
                return resp
        except Exception:
            _LOGGER.debug(f"Error while posting to {url}", exc_info=True)
            # authentication may solve the issue, backoff will resend the request
            await self.authenticate()
            raise  # re-raise the exception

    @backoff.on_exception(backoff.expo, Exception, max_tries=BACKOFF_TRIES)
    async def authenticate(self):
        """Login to BMR controller. Note that BMR controller is using a kinda
        weird and insecure authentication mechanism - it looks like it's
        just remembering the username and IP address of the logged-in user.
        """

        def bmr_hash(value: str) -> str:
            output = ""
            day = date.today().day
            for c in value:
                tmp = ord(c) ^ (day << 2)
                output = output + hex(tmp)[2:].zfill(2)
            return output.upper()
        _LOGGER.debug("Authenticating to BMR controller")
        data = {"loginName": bmr_hash(self._user), "passwd": bmr_hash(self._password)}
        async with self.session.post(f"{self.base_url}/menu.html", data=FormData(data)) as response:
            if "res_error_title" in await response.text():
                raise AuthException("Authentication failed, check username/password")
            return True

    @cached(LRUCache(maxsize=1))
    async def getUniqueId(self):
        """Return unique ID of the entity.

        The BMR HC64 API doesn't provide anything that could be used as a
        unique ID, such as serial number. Therefore we have to generate it
        from something that doesn't usually change - such as circuit names.

        Note that this is more like a unique ID for the whole HC64
        controller, not a unique ID of a circuit.
        """
        return sha256(
            b"\0".join([name.encode("utf-8") for name in await self.getCircuitNames()])
        ).hexdigest()[:8]

    @cached(LRUCache(maxsize=1))
    @backoff.on_exception(backoff.expo, Exception, max_tries=BACKOFF_TRIES)
    async def getNumCircuits(self):
        """Get the number of heating circuits."""
        return int(await self._post("numOfRooms"))

    @cached(LRUCache(maxsize=1))
    async def getCircuitNames(self) -> List[str]:
        """Get the names of all heating circuits."""
        text = await self._post("listOfRooms")
        return [
            text[i: i + 13].strip() for i in range(0, len(text), 13)
        ]
        # Example: F01 Byt      F02 Pokoj    F03 Loznice  F04 Koupelna F05 Det pokojF06 Chodba   F07 Kuchyne  F08 Obyvak   R01 Byt      R02 Pokoj    R03 Loznice  R04 Koupelna R05 Det pokojR06 Chodba   R07 Kuchyne  R08 Obyvak  # noqa

    async def setManualTemp(self, circuit_id: int, new_target: float, current_target: Optional[float] = None) -> bool:
        """Set manual temperature for a circuit. Behind the scenes, only a request for an offset is being sent.

        If you ommit current_target, it is retrieved from the unit. If you want to quickly set a certain offset,
        you can provide the current_target yourself. (e.g. if new_target == current_target, an offset of "0" from
        the scheduled temprature will be set).
        
        Args:
            circuit_id: ID of the circuit.
            new_target: New target temperature.
            current_target: Current target temperature (optional).

        Returns:
            True if the request was successful, False otherwise
        """
        if current_target is None:  # if current_target temperature is not provided, try to get it
            current_settings = await self.getCircuit(circuit_id, skip_override_check=True)
            # the caveat is that the target already contains the user offset, so we need to subtract it...
            if current_settings["scheduled_temperature"] is not None:
                current_target = current_settings["scheduled_temperature"]
            else:
                current_target = new_target  # this will create a zero offset
        offset = new_target - current_target
        param = "{:02}{}{:03}".format(circuit_id, "-" if offset < 0 else "0", int(abs(offset)*10))

        data = {"manualTemp": param}
        _LOGGER.debug(f"Setting manual temperature for circuit {circuit_id} to {new_target}"
                      f"from {current_target} using offset {offset}")
        return "true" in await self._post("saveManualTemp", data)

    async def setTemperatureOverride(self, circuit_id: int, temperature: float, duration: Optional[float] = None):
        """Set temperature override for a circuit.

        Args:
            circuit_id: ID of the circuit.
            temperature: New target temperature.
            duration: Duration of the override in seconds. If not provided, the override will be active indefinitely."""
        _LOGGER.debug(f"Setting temperature override for circuit {circuit_id} to {temperature}°C")
        now = datetime.now()
        if duration is not None:
            stop_at = now + timedelta(seconds=duration)
        else:
            stop_at = None

        self.overrides[circuit_id] = TemperatureOverride(
            temperature=temperature,
            created_at=now,
            stop_at=stop_at,
        )
        await self.storeOverrides()
        await self.setManualTemp(circuit_id, temperature)

    async def storeOverrides(self):
        """Store overrides to the store."""
        if self.overrides_store is not None:
            await self.overrides_store.async_save(dict((k, v.serialize()) for k, v in self.overrides.items()))
        else:
            _LOGGER.warning("No overrides store provided, overrides won't be stored.")

    async def removeTemperatureOverride(self, circuit_id: int):
        """Remove temperature override for a circuit."""
        if circuit_id in self.overrides:
            # this will revoke the override in the next getCircuit call
            self.overrides[circuit_id].stop_at = datetime.now()-timedelta(seconds=1)
            # do the call
            await self.getCircuit(circuit_id)
        else:
            # there was no override from our side, must have been from the physical buttons or BMR's web UI
            # let's remove the user offset then
            await self.setManualTemp(circuit_id, 0, 0)

    @backoff.on_exception(backoff.expo, Exception, max_tries=BACKOFF_TRIES)
    async def getCircuit(self, circuit_id: int, skip_override_check: bool = False) -> "BmrCircuitData":
        """Get circuit status.

        Raw data returned from server:

          1Pokoj 202 v  021.7+12012.0000.000.0000000000

        Byte offsets of:
          POS_ENABLED = 0
          POS_NAME = 1
          POS_ACTUALTEMP = 14
          POS_REQUIRED = 19
          POS_REQUIREDALL = 22
          POS_USEROFFSET = 27
          POS_MAXOFFSET = 32
          POS_S_TOPI = 36
          POS_S_OKNO = 37
          POS_S_KARTA = 38
          POS_VALIDATE = 39
          POS_LOW = 42
          POS_LETO = 43
          POS_S_CHLADI = 44

        Args:
            circuit_id: ID of the circuit.
            skip_override_check: Skip checking for temperature overrides - useful if you want to just
                get the circuit info, but don't want the control loop to kick in, since it is done elsewhere.

        Returns:
            Circuit status.
        """
        data = {"param": circuit_id}
        room_status_text = await self._post("wholeRoom", data)

        match = re.match(
            r"""
                (?P<enabled>.{1})                  # Whether the circuit is enabled
                (?P<name>.{13})                    # Name of the circuit
                (?P<temperature>.{5})              # Current temperature
                (?P<scheduled_temperature>.{3})    # Scheduled temperature
                (?P<target_temperature>.{5})       # Target temperature (incl. user offset)
                (?P<user_offset>.{5})              # Current temperature offset set by user
                (?P<max_offset>.{4})               # Max temperature offset
                (?P<heating>.{1})                  # Whether the circuit is currently heating
                (?P<window_heating>.{1})
                (?P<card>.{1})
                (?P<warning>.{3})                  # Warning code
                (?P<low_mode>.{1})                 # Whether the circuit is assigned to low mode and low mode is active
                (?P<summer_mode>.{1})              # Whether the circuit is assigned to summer mode and summer mode
                                                   # is active
                (?P<cooling>.{1})                  # Whether the circuit is cooling (only water-based circuits)
                """,
            room_status_text,
            re.VERBOSE,
        )
        if not match:
            raise Exception(
                "Server returned malformed data: {}. Try again later".format(
                    room_status_text
                )
            )
        room_status = match.groupdict()

        # Sometimes some of the values are malformed, i.e. "00\x00\x00\x00" or "-1-1-"
        result: BmrCircuitData = {
            "id": circuit_id,
            "enabled": bool(int(room_status["enabled"])),
            "name": room_status["name"].rstrip(),
            "temperature": None,
            "target_temperature": None,
            "scheduled_temperature": None,
            "user_offset": None,
            "max_offset": None,
            "heating": False,
            "warning": 0,
            "cooling": False,
            "low_mode": False,
            "summer_mode": False,
            "target_temperature_raw": None,
            "override_updating": False,
            "low_mode_assigned": None,
            "summer_mode_assigned": None,
        }

        for key in (
            "scheduled_temperature",
            "target_temperature",
            "temperature",
            "user_offset",
            "max_offset",
        ):
            try:
                result[key] = float(room_status[key])
            except ValueError:
                pass

        for key in (
            "heating",
            "cooling",
            "low_mode",
            "summer_mode",
        ):
            try:
                result[key] = bool(int(room_status[key]))
            except ValueError:
                pass

        for key in (
            "warning",
        ):
            try:
                result[key] = int(room_status[key])
            except ValueError:
                pass
        # track the original target for debugging
        result["target_temperature_raw"] = result["target_temperature"]

        # the target temperature computation on BMR is slow, so we use scheduled temperature + offset
        result["target_temperature"] = target = result["scheduled_temperature"] + result["user_offset"]
        # if overrides are active, we will change what's shown again and even try to enforce the override
        if not skip_override_check and circuit_id in self.overrides:
            _LOGGER.debug(f"Circuit {circuit_id} has an override")
            try:
                override = self.overrides[circuit_id]
                result["target_temperature"] = override.temperature
                _LOGGER.debug(f"Reported temperature changed from {result['target_temperature_raw']}/{target}"
                              f"to {override.temperature}.")
                if override.stop_at is None or override.stop_at > datetime.now():
                    # target_temperature should be set to the override's temperature
                    # but the unit is very slow and sometimes changes offset before the target temperature which
                    # messes things up. So we take our time...

                    if target != override.temperature and \
                       override.last_set < (datetime.now()-timedelta(seconds=TEMPERATURE_OVERRIDE_CHECK_DELAY)):
                        override.last_set = datetime.now()  # no need to storeOverrides() with this minor change
                        _LOGGER.debug(f"Override check shows that the target temperature for circuit {circuit_id} "
                                      f"should be {override.temperature} instead of "
                                      f"{result['target_temperature_raw']}/{target}")
                        # we have everything we need to set the manual offset of the temperature
                        await self.setManualTemp(circuit_id, override.temperature, result["scheduled_temperature"])
                elif override.stop_at <= datetime.now():  # the override has expired
                    # the controller can take some time updating the offset,
                    # but we will already force it to report a zero offset (meaning we are back on scheduled temp)
                    # FIXME this can misreport a user offset that was set using the physical buttons or BMR's web UI,
                    # but we can't do anything about it. The BMR web itself gets confused while updating the offset.
                    # It would be nice to have an "unknown" state for this, but I don't think we can do that.
                    result["user_offset"] = 0
                    result["target_temperature"] = result["scheduled_temperature"]
                    if override.disabled_at is None:  # we have not disabled the override yet, so let's do it
                        # go back to the original temperature by setting zero offset
                        _LOGGER.debug(f"Override is over for circuit {circuit_id}, setting zero offset.")
                        await self.setManualTemp(circuit_id, 0, 0)
                        # mark the override as disabled
                        self.overrides[circuit_id].disabled_at = datetime.now()
                        await self.storeOverrides()
                    elif override.disabled_at < (datetime.now()-timedelta(seconds=TEMPERATURE_OVERRIDE_CHECK_DELAY)):
                        # the override will stay in the disabled state for some time to enforce the 0 offset
                        # once the override expires completely, we have to remove the override from the list
                        _LOGGER.debug(f"Override for {circuit_id} expired and will be deleted.")
                        del self.overrides[circuit_id]
                        await self.storeOverrides()
            except Exception:
                _LOGGER.exception(f"Error while processing override for circuit {circuit_id}")
        return result

    @cached(TTLCache(maxsize=CACHE_DEFAULT_MAXSIZE, ttl=CACHE_DEFAULT_TTL))
    async def getSchedules(self):
        """Load schedules."""
        response_text = await self._post("listOfModes")
        return [x.rstrip() for x in re.findall(r".{13}", response_text)]

    @cached(TTLCache(maxsize=CACHE_DEFAULT_MAXSIZE, ttl=CACHE_DEFAULT_TTL))
    async def getSchedule(self, schedule_id: int):
        """Load schedule settings."""
        data = {"modeID": "{:02d}".format(schedule_id)}
        response_text = await self._post("loadMode", data)

        # Example: 1 Byt        00:0002106:0002112:0002121:00021
        match = re.match(
            r"""
                (?P<name>.{13})                          # schedule name
                (?P<timetable>(\d{2}:\d{2}\d{3}){1,8})?  # time and target temperature
            """,
            response_text,
            re.VERBOSE,
        )
        if not match:
            raise Exception(
                "Server returned malformed data: {}. Try again later".format(
                    response_text
                )
            )
        schedule = match.groupdict()
        timetable = None
        if schedule["timetable"]:
            timetable = [
                {"time": x[0], "temperature": int(x[1])}
                for x in re.findall(r"(\d{2}:\d{2})(\d{3})", schedule["timetable"])
            ]

        return {
            "id": schedule_id,
            "name": schedule["name"].rstrip(),
            "timetable": timetable,
        }

    async def setSchedule(self, schedule_id: int, name: str, timetable):
        """Save schedule settings. Name is the new schedule name. Timetable is
        a list of tuples of time and target temperature. When the schedule is
        associated with a circuit BMR heating controller will use the
        schedule timetable to set the target temperature at the specified
        time. Note that the first entry in the timetable must be always for
        time "00:00".
        """
        if timetable[0]["time"] != "00:00":
            raise Exception("First timetable entry must be for time 00:00")

        data = {
            "modeSettings": "{:02d}{:13.13}{}".format(
                schedule_id,
                name[:13],
                "".join(
                    [
                        "{}{:03d}".format(item["time"], int(item["temperature"]))
                        for item in timetable
                    ]
                ),
            )
        }
        return "true" in await self._post("saveMode", data)

    async def deleteSchedule(self, schedule_id: int):
        """Delete schedule."""
        data = {"modeID": "{:02d}".format(schedule_id)}
        return "true" in await self._post("deleteMode", data)

    @cached(TTLCache(maxsize=CACHE_DEFAULT_MAXSIZE, ttl=CACHE_DEFAULT_TTL))
    @backoff.on_exception(backoff.expo, Exception, max_tries=BACKOFF_TRIES)
    async def getSummerMode(self):
        """Return True if summer mode is currently activated."""
        val = await self._post("loadSummerMode")
        try:
            return not bool(int(val))  # for some reason 0 == summer mode on, 1 == summer mode off
        except ValueError:
            _LOGGER.warning("Summer mode is not a boolean.")
            return False

    async def setSummerMode(self, value: bool):
        """Enable or disable summer mode."""
        data = {"summerMode": "0" if value else "1"}
        return "true" in await self._post("saveSummerMode", data)

    @cached(TTLCache(maxsize=CACHE_DEFAULT_MAXSIZE, ttl=CACHE_DEFAULT_TTL))
    async def getSummerModeAssignments(self):
        """Load circuit summer mode assignments, i.e. which circuits will be
        affected by summer mode when it is turned on.
        """
        response_text = await self._post("letoLoadRooms")
        try:
            return [bool(int(x)) for x in list(response_text)]
        except ValueError:
            raise Exception(
                "Server returned malformed data: {}. Try again later".format(
                    response_text
                )
            )

    async def setSummerModeAssignments(self, circuits: List[int], value: bool) -> Optional[List[bool]]:
        """Assign or remove specified circuits to/from summer mode. Leave
        other circuits as they are.
        """
        _LOGGER.debug(f"Setting summer mode for circuits {circuits} to {value}")
        assignments = await self.getSummerModeAssignments()

        for circuit_id in circuits:
            assignments[circuit_id] = value

        data = {"value": "".join([str(int(x)) for x in assignments])}
        if "true" in await self._post("letoSaveRooms", data):
            return assignments
        else:
            return None

    @cached(TTLCache(maxsize=CACHE_DEFAULT_MAXSIZE, ttl=CACHE_DEFAULT_TTL))
    @backoff.on_exception(backoff.expo, Exception, max_tries=BACKOFF_TRIES)
    async def getLowMode(self) -> "BmrLowModeData":
        """Get status of the LOW mode."""
        response_text = await self._post("loadLows")
        # The response is formatted as "<temperature><start_datetime><end_datetime>", let's parse it
        match = re.match(
            r"""
            (?P<temperature>\d{3})
            (?P<start_datetime>\d{4}-\d{2}-\d{2}\d{2}:\d{2})?
            (?P<end_datetime>\d{4}-\d{2}-\d{2}\d{2}:\d{2})?
            """,
            response_text,
            re.VERBOSE,
        )
        if not match:
            raise Exception(
                "Server returned malformed data: {}. Try again later".format(
                    response_text
                )
            )
        low_mode = match.groupdict()
        result: BmrLowModeData = {
            "enabled": low_mode["start_datetime"] is not None,
            "temperature": int(low_mode["temperature"]),
            "start_date": None,
            "end_date": None,
        }
        if low_mode["start_datetime"]:
            result["start_date"] = datetime.strptime(
                low_mode["start_datetime"], "%Y-%m-%d%H:%M"
            )
        if low_mode["end_datetime"]:
            result["end_date"] = datetime.strptime(
                low_mode["end_datetime"], "%Y-%m-%d%H:%M"
            )
        return result

    async def setLowMode(
        self,
        enabled: bool,
        temperature: Optional[float] = None,
        start_datetime: Optional[datetime] = None,
        end_datetime: Optional[datetime] = None
    ):
        """Enable or disable LOW mode. Temperature specified the desired
        temperature for the LOW mode.

        - If start_date is provided enable LOW mode indefiniitely.
        - If also end_date is provided end the LOW mode at this specified date/time.
        """
        if start_datetime is None:
            start_datetime = datetime.now()

        if temperature is None:
            temperature = (await self.getLowMode())["temperature"]

        data = {
            "lowData": "{:03d}{}{}".format(
                int(temperature),
                (
                    start_datetime.strftime("%Y-%m-%d%H:%M")
                    if enabled and start_datetime
                    else " " * 15
                ),
                (
                    end_datetime.strftime("%Y-%m-%d%H:%M")
                    if enabled and end_datetime
                    else " " * 15
                ),
            )
        }
        return "true" in await self._post("lowSave", data)

    @cached(TTLCache(maxsize=CACHE_DEFAULT_MAXSIZE, ttl=CACHE_DEFAULT_TTL))
    async def getLowModeAssignments(self):
        """Load circuit LOW mode assignments, i.e. which circuits will be
        affected by LOW mode when it is turned on.
        """
        response_text = await self._post("lowLoadRooms")
        return [bool(int(x)) for x in list(response_text)]

    async def setLowModeAssignments(self, circuits, value) -> Optional[List[bool]]:
        """Assign or remove specified circuits to/from LOW mode. Leave
        other circuits as they are.
        """
        assignments = await self.getLowModeAssignments()

        for circuit_id in circuits:
            assignments[circuit_id] = value

        data = {"value": "".join([str(int(x)) for x in assignments])}
        if "true" in await self._post("lowSaveRooms", data):
            return assignments
        else:
            return None

    @cached(TTLCache(maxsize=CACHE_DEFAULT_MAXSIZE, ttl=CACHE_DEFAULT_TTL))
    async def getCircuitSchedules(self, circuit_id):
        """Load circuit schedule assignments, i.e. which schedule is assigned
        to what day. It is possible to set different schedule for up 21
        days.
        """
        data = {"roomID": "{:02d}".format(circuit_id)}
        response_text = await self._post("roomSettings", data)

        # Example: 0140-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1
        match = re.match(
            r"""
                (?P<starting_day>\d{2})        # Which schedule should be the
                                               # first to start with. Can be either
                                               # "01", "08" or "15". Note that
                                               # there can't be any unconfigured
                                               # gaps (missing schedules) in any
                                               # days between day 1 and the
                                               # starting day.
                (?P<day_schedules>([-\d]{2}){21})  # schedule IDs + indicator of the
                                               # currently active schedule
                """,
            response_text,
            re.VERBOSE,
        )
        if not match:
            raise Exception(
                "Server returned malformed data: {}. Try again later".format(
                    response_text
                )
            )
        circuit_schedules = match.groupdict()
        result = {
            "starting_day": int(circuit_schedules["starting_day"]),
            "current_day": None,
            "day_schedules": [],
        }
        for idx, schedule_id in enumerate(
            re.findall(r"[-\d]{2}", circuit_schedules["day_schedules"])
        ):
            schedule_id = int(schedule_id)
            if schedule_id == -1:
                # The list of schedules must be continuous, there aren't
                # allowed any "gaps". So this is the last entry, following items
                # have to be are "-1" as well.
                break
            else:
                result["day_schedules"].append(
                    schedule_id & 0b00011111
                )  # schedule ID is in the lower 5 bits
                if (
                    schedule_id & 0b00100000 == 0b00100000
                ):  # 6th rightmost bit is indicator of currently active schedule
                    result["current_day"] = idx + 1
        return result

    async def setCircuitSchedules(self, circuit_id, day_schedules, starting_day=1):
        """Assign circuits schedules. It is possible to have a different
        schedule for up to 21 days.
        """
        # Make sure that day_schedules is list with length 21, if not append None's at the end
        day_schedules += [None for _ in range(21 - len(day_schedules))]

        # Make sure there are no undefined gaps
        for idx in range(len(day_schedules) - 1):
            if day_schedules[idx] is None and day_schedules[idx + 1] is not None:
                raise Exception("Circuit schedules can't have any undefined gaps.")

        # Example: 000108-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1-1
        data = {
            "roomSettings": "{:02d}{:02d}{}".format(
                circuit_id,
                starting_day,
                "".join(
                    ["{:02d}".format(x if x is not None else -1) for x in day_schedules]
                ),
            )
        }
        return "true" in await self._post("saveAssignmentModes", data)

    @cached(TTLCache(maxsize=1, ttl=CACHE_DEFAULT_TTL))
    @backoff.on_exception(backoff.expo, Exception, max_tries=BACKOFF_TRIES)
    async def getHDO(self):
        """Get status of the HDO (remote grid control) mode."""
        return await self._post("loadHDO") == "1"

    @cached(TTLCache(maxsize=1, ttl=CACHE_DEFAULT_TTL))
    async def getNumOfRollerShutters(self) -> int:
        """
        Get the number of installed roller shutters.
        Example call:
        curl 'http://bmr-hc64.local/numOfRollerShutters' -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' \
        --data-raw 'param=+'
        """
        return int(await self._post("numOfRollerShutters"))

    @cached(TTLCache(maxsize=1, ttl=CACHE_DEFAULT_TTL))
    async def getListOfRollerShutters(self) -> list[str]:
        """
        Get the names of installed roller shutters as a list.
        Example API response text: 'Kuchyna      Jedalen      Terasa velke Terasa male  Obyvacka 1   Obyvacka 2   Hostovska    Pracovna     Kupelna hore Spalna       Izba velka   Izba mala    '
        Example call:
        curl 'http://bmr-hc64.local/listOfRollerShutters' -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' --data-raw 'param=+'
        """
        response_text = await self._post("listOfRollerShutters")
        return [
            response_text[i: i + 13].strip() for i in range(0, len(response_text), 13)
        ]

    @cached(TTLCache(maxsize=1, ttl=CACHE_DEFAULT_TTL))
    async def getWindSensorStatus(self):
        """
        Example API call:
        curl 'http://bmr-hc64.local/windSensorStatus' -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' --data-raw 'param=+'
        Example response:
        0000000001111111111111111111111111111111100000000000
        """
        return await self._post("windSensorStatus")

    @cached(TTLCache(maxsize=1, ttl=CACHE_DEFAULT_TTL))
    async def getWholeRollerShutter(self, shutter_id: int) -> dict:
        """
        Get the status of a single roller shutter.
        Example API call:
        curl 'http:///bmr-hc64.local/wholeRollerShutter' -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' --data-raw 'rollerShutter=6'
        Example API response:
        '1Kuchyna      0000010000000000000'
        """
        assert 0 <= shutter_id <= 32
        data = {"rollerShutter": str(shutter_id)}
        response_text = await self._post("wholeRollerShutter", data)

        # TODO how is the response formatted?
        ret = {
            "name": response_text[1:14].strip(),
            "pos": int(response_text[14:15]),
            "tilt": int(response_text[15:17]),
        }
        return ret

    async def saveManualChange(self, shutter_id: int, pos: int, tilt: int) -> bool:
        """
        Set shutter blind to a specific position.

        Formatting of the request data:
        0-1: blind ID, starts from 0, simple decimal number, no bitmask - can't change multiple blinds with a single call
        2: position. It maps from 100 fully open to 0 fully closed to:
            0: open / otevreno (fully pulled up)
            1: closed / zavreno (fully lowered down)
            2: sits / sterbiny (3/4 down )
            3: half / mezipoloha (in the middle)
        3-4: tilt: It maps from 100 fully open to 0 fully closed to: <0 - 10>
            0: open - segments horizontally, mamimum light passing through
            10: closed - segments vertically, mimimum light pasing through
            One step translates to a minimal impulse to the motors to open/close the blinds.
            With my motors, 5 steps are enough to go from fully open to fully closed.
            Position is relative. When going from 10 when closed to 5, blinds fully open, Same when going from 5 to 0.

        Example call:
        curl 'http://bmr-hc64.local/saveManualChange' -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' \
        --data-raw 'manualChange=07200'
        """
        try:
            assert 0 <= shutter_id <= 32
            assert 0 <= pos <= 100
            assert 0 <= tilt <= 100

            bmr_pos: int = 1
            if pos > 90:
                bmr_pos = 0
            elif pos > 45:
                bmr_pos = 3
            elif pos > 15:
                bmr_pos = 2

            bmr_tilt: int = int((100 - tilt) / 10)
            data = {"manualChange": f"{shutter_id:02d}{bmr_pos:01d}{bmr_tilt:02d}"}
            response_text = await self._post("saveManualChange", data)
            _LOGGER.debug(f"data {data} response text {response_text}")
            ret = "true" in response_text
            return ret
        except Exception as e:
            print(e)
            return False

    @cached(TTLCache(maxsize=1, ttl=CACHE_DEFAULT_TTL))
    @backoff.on_exception(backoff.expo, Exception, max_tries=BACKOFF_TRIES)
    async def getVentilation(self) -> Dict[str, int]:
        """
        Get the status of the ventilation system.
        """
        response_text = await self._post("rekuperaceStatus")
        ppm = int(response_text[5:9])
        power = int(response_text[:3])

        return {
            "power": power,
            "ppm": ppm
        }

    async def getAllData(self) -> "BmrAllData":
        """Get all data from the BMR controller."""
        # iterate over all circuits and get their data
        circuits: List[BmrCircuitData] = []
        _LOGGER.debug("Getting all data from the BMR controller")
        num_circuits = await self.getNumCircuits()
        _LOGGER.debug(f"Found {num_circuits} circuits")
        for circuit_id in range(num_circuits):
            _LOGGER.debug(f"Getting data for circuit {circuit_id}")
            circuits.append(await self.getCircuit(circuit_id))
        try:
            hdo = await self.getHDO()
        except Exception:
            hdo = None
        try:
            ventilation = await self.getVentilation()
        except Exception:
            ventilation = None
        try:
            summer_mode = await self.getSummerMode()
        except Exception:
            summer_mode = None
        try:
            low_mode = await self.getLowMode()
        except Exception:
            low_mode = None

        try:
            for idx, assigned in enumerate(await self.getSummerModeAssignments()):
                circuits[idx]["summer_mode_assigned"] = assigned
        except Exception:
            pass
        try:
            for idx, assigned in enumerate(await self.getLowModeAssignments()):
                circuits[idx]["low_mode_assigned"] = assigned
        except Exception:
            pass

        _LOGGER.debug(f"Got all data: {circuits}, {hdo}, {ventilation}, {summer_mode}, {low_mode}")
        _LOGGER.debug(f"Current overrides: {self.overrides}")
        return {
            "circuits": circuits,
            "hdo": hdo,
            "ventilation": ventilation,
            "summer_mode": summer_mode,
            "low_mode": low_mode,
        }


class BmrCircuitData(TypedDict):
    id: int
    enabled: bool   # True if the circuit is enabled
    name: str       # name of the circuit
    temperature: Optional[float]  # current temperature
    target_temperature: Optional[float]  # target temperature, including user_offset if applied
    scheduled_temperature: Optional[float]  # scheduled temperature
    user_offset: Optional[float]  # manual offset applied by the user to the scheduled temperature
    max_offset: Optional[float]   # maximum user offset allowed by the system
    heating: bool   # True if the circuit is currently heating
    warning: int    # warning code
    cooling: bool   # True if the circuit is currently cooling
    low_mode: bool  # True if low mode is applied to the circuit
    summer_mode: bool  # True if summer mode is applied to the circuit
    target_temperature_raw: Optional[float]  # the temperature as the unit passes it, no the modifications by overrides
    override_updating: bool  # True if the temperature override is waiting to be set and reporting may be inaccurate
    low_mode_assigned: Optional[bool]  # True if the circuit is assigned to low mode
    summer_mode_assigned: Optional[bool]  # True if the circuit is assigned to summer mode


class BmrLowModeData(TypedDict):
    enabled: bool
    temperature: int
    start_date: Optional[datetime]
    end_date: Optional[datetime]


class BmrAllData(TypedDict):
    circuits: List[BmrCircuitData]
    hdo: Optional[bool]
    ventilation: Optional[Dict[str, int]]
    summer_mode: Optional[bool]
    low_mode: Optional[BmrLowModeData]
