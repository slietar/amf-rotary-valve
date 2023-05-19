import asyncio
from dataclasses import dataclass, field
from typing import Self

from .device import AMFDevice, AMFDeviceConnectionError


@dataclass
class HierarchyNode:
  value: list[str]
  children: 'list[Self]' = field(default_factory=list)

  def format(self, *, prefix: str = str()):
    return ("\n" + prefix).join(self.value) + str().join([
      "\n" + prefix
        + ("└── " if (last := (index == (len(self.children) - 1))) else "├── ")
        + child.format(prefix=(prefix + ("    " if last else "│   ")))
        for index, child in enumerate(self.children)
    ])


async def main():
  root = HierarchyNode(["."])

  for device_info in AMFDevice.list():
    try:
      async with device_info.create() as device:
        valve = await device.get_valve()

        root.children.append(HierarchyNode([
          "Rotary valve",
          f"Address: {device.address}",
          f"Unique id: {await device.get_unique_id()}",
          f"Current valve: {valve if valve is not None else '<uninitialized>'}",
          f"Valve count: {await device.get_valve_count()}"
        ]))
    except* AMFDeviceConnectionError:
      pass

  if root.children:
    print(root.format())
  else:
    print("No device found.")


asyncio.run(main())
