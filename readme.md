# amf-rotary-valve

This Python package provides control of [AMF rotary valves](https://amf.ch/product/oem-rotary-valve/).


## Installation

```sh
$ pip install amf-rotary-valve

# List available devices
$ python -m amf_rotary_valve
```


## Usage

```py
from amf_rotary_valve import AMFDevice

device = OkolabDevice(address="COM3")
device = OkolabDevice(address="/dev/tty.usbmodem1101")
```

```py
async with device:
  await device.home()

  valve_count = await device.get_valve_count()
  current_valve = await device.get_valve()

  # Rotate to the next valve
  await device.rotate(current_valve % valve_count + 1)
```

```py
for info in OkolabDevice.list():
  async with (device := info.create())
    print(await device.get_unique_id())
```
