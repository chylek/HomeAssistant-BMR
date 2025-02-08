# HomeAssistant-BMR

## IMPORTANT INFO
This is a fork and WORK IN PROGRESS. The original plugin is available [here](https://github.com/slesinger/HomeAssistant-BMR)
Documentation is not fully updated yet, but the plugin is working with the latest Home Assistant version and
the configuration is done via the UI.

Temperature entities are gone, only climate entities and binary sensors are available.
Every circuit is automatically added as a climate entity, and a binary sensor is added for HDO state.
Schedules cannot be changed from Home Assistant, only the target temperature can be changed. 

**The approach for target temperature override is very different from the original plugin.**
The integration now uses the BMR-native manual temperature offset feature. The integration checks periodically
if the temperature override still applies the correct offset from the schedule. If not, it will reapply the override. This means the target temperature cannot be changed using the BMR controller or physical controls on
the temperature sensors. You have to change it in Home Assistant, otherwise the integration will keep changing it back to its value. You should be always able to remove the override by switching the Climate entity to Auto mode.

Since the BMR Controller works a bit slow when updating the target temperature and the user offset, the integration
takes its time enforcing it (up to 5 minutes). This is to prevent the controller from getting confused and not applying the offset correctly. 

There are also other changes under the hood - a DataUpdateCoordinator is used to manage the updates efficiently, the plugin is now async, the client library is built-in.

## About the Project
This integration allows connecting the BMR HC64 controller to Home Assistant, enabling heating control, ventilation management, and sensor readings via the Home Assistant UI.

## Installation
### HACS
The recommended installation method is via [HACS](https://github.com/custom-components/hacs).
1. Add the repository to HACS.
2. Install the BMR HC64 integration.
3. Restart Home Assistant.

### Manual Installation
1. Download or clone this repository.
2. Copy the `custom_components/bmr` folder to your Home Assistant `config/custom_components/` directory.
3. Restart Home Assistant.

## Configuration
The integration is configurable through the Home Assistant UI.
1. Navigate to **Settings -> Devices & Services -> Add Integration**.
2. Select **BMR HC64**.
3. Enter the controller’s URL, username, and password. Check the box if your system supports cooling (and not only heating). 
4. Complete the setup.

## Provided Entities
### Binary Sensors
- **HDO - Low Tariff**: Indicates the state of the low electricity tariff.

### Sensors
- **Ventilation**: Ventilation speed.
- **CO2**: CO2 level in PPM.

### Climate Devices
- Each heating circuit is represented as a climate entity in Home Assistant.
- Supported modes:
  - **Auto**: Uses the schedule configured in the BMR HC64 controller.
  - **Heat**: Manual target temperature adjustment using a temperature offset. There is no point in setting this mode directly - set the target temperature instead!
  - **Off**: Adds the circuit to the "Summer" mode and enables the Summer mode globally if it wasn't active yet – basically turns off the circuits.
  - **Away**: Adds the circuit to the "Low" mode and enables the Low mode globally if it wasn't active yet. The integration doesn't support changing the Low mode's temperature.

### Switches
- **Away Mode**: A switch to enable or disable Away/Low mode globally for the system. This keeps all the circuit assignments intact.
- **Summer Mode**: A switch to enable or disable Summer mode globally for the system. This keeps all the circuit assignments intact.

## License
This project is licensed under the **Apache 2.0** license.

## Links
- Documentation: [GitHub](https://github.com/chylek/HomeAssistant-BMR)
- Home Assistant: [home-assistant.io](https://www.home-assistant.io/)

