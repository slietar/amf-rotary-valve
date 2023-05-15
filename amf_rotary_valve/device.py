import asyncio
import builtins
from asyncio import Future
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional, overload

import serial.tools.list_ports
from serial import Serial
from serial.serialutil import SerialException

from .util import aexit_handler


Datatype = type[bool] | type[int]

class AMFDeviceConnectionError(Exception):
  pass


@dataclass(frozen=True, kw_only=True)
class AMFDeviceInfo:
  address: str

  def create(self):
    return AMFDevice(self.address)


class AMFDevice:
  def __init__(self, address: str):
    """
    Constructs an `AMFDevice` instance and opens the connection to the device.

    Parameters
      address: The address of the device, such as `COM3` or `/dev/tty.usbmodem1101`.

    Raises
      AMFDeviceConnectionError: If the device is unreachable.
    """

    self.address = address

    try:
      self._serial: Optional[Serial] = Serial(
        baudrate=9600,
        port=address
      )
    except (OSError, SerialException) as e:
      raise AMFDeviceConnectionError from e

    self._busy = False
    self._closing = False
    self._busy_future: Optional[Future[Any]] = None
    self._query_futures = deque[Future[Any]]()

  async def _read_loop(self):
    loop = asyncio.get_event_loop()

    try:
      while True:
        assert self._serial
        serial = self._serial

        # Call _receive() to process the received data
        self._receive((await loop.run_in_executor(None, lambda: serial.read_until(b"\n")))[0:-2])
    finally:
      self._read_task = None

      # Raise exceptions in all the current futures

      if self._busy_future:
        self._busy_future.set_exception(AMFDeviceConnectionError())

      if self._query_futures:
        for future in self._query_futures:
          future.set_exception(AMFDeviceConnectionError())

  @overload
  async def _query(self, command: str, dtype: type[bool]) -> bool:
    pass

  @overload
  async def _query(self, command: str, dtype: type[int]) -> int:
    pass

  @overload
  async def _query(self, command: str, dtype = None) -> str:
    pass

  async def _query(self, command: str, dtype: Optional[type[bool] | type[int]] = None):
    if self._closing:
      raise AMFDeviceConnectionError

    future = Future()
    self._query_futures.append(future)

    loop = asyncio.get_event_loop()

    try:
      try:
        assert (serial := self._serial)
        await loop.run_in_executor(None, lambda: serial.write(f"/_{command}\r".encode("utf-8")))
      except:
        # Remove the future if the write failed
        self._query_futures.remove(future)
        raise

      return self._parse(await asyncio.wait_for(asyncio.shield(future), timeout=2.0), dtype=dtype)
    except (SerialException, asyncio.TimeoutError) as e:
      raise AMFDeviceConnectionError from e

  def _parse(self, data: bytes, dtype: Optional[Datatype] = None):
    response = data[3:-1].decode("utf-8")

    match dtype:
      case builtins.bool:
        return (response == "1")
      case builtins.int:
        return int(response)
      case _:
        return response

  def _receive(self, data: bytes):
    was_busy = self._busy
    self._busy = (data[2] & (1 << 5)) < 1

    if self._busy_future and was_busy and (not self._busy):
      self._busy_future.set_result(data)
      self._busy_future = None
    else:
      query_future = self._query_futures.popleft()
      query_future.set_result(data)

  async def _run(self, command: str):
    if self._closing or (not self._read_task):
      raise AMFDeviceConnectionError

    while self._busy_future:
      await self._busy_future

    future = Future[Any]()
    self._busy_future = future

    # If an exception is raised during _query(), it is impossible to know whether to request has been acknowledged or not, there we keep _busy_future to a meaningful value.
    await self._query(command)
    await asyncio.shield(future)

  async def get_unique_id(self):
    """
    Returns the unique id of this device.

    Returns
      The unique id of this device, such as `...`.
    """

    return await self._query("?9000")

  async def get_valve(self):
    """
    Returns the current valve position.

    Returns
      The current valve position, starting at 1, or `None` if the device has not been initialized.
    """

    res = await self._query("?6", dtype=int)
    return res if res != 0 else None

  async def get_valve_count(self):
    """
    Returns the number of valves available on the rotary valve.
    """

    return await self._query("?801", dtype=int)

  async def home(self):
    """
    Initializes the rotary valve.

    The rotary valve must be initialized every time it is powered on. If it has already been initialized when calling this function, it will be re-initialized. The initialization status can be obtained by calling `get_valve()`, which returns `None` if the device has not yet been initialized.
    """

    await self._run("ZR")

  async def rotate(self, valve: int, /):
    """
    Rotates the rotary valve to a new valve position.

    A 360º rotation occurs if `valve` is already the current position. To avoid this behavior, check the current position with `get_valve()`.

    Parameters
      valve: The valve position to rotate to, starting at 1.
    """

    await self._run(f"b{valve}R")

  async def wait(self, delay: float, /):
    """
    Instructs the rotary valve to wait for a fixed time duration.

    Parameters
      delay: The delay, in seconds. The resolution is 1 ms.
    """

    await self._run(f"M{round(delay * 1000)}R")

  async def __aenter__(self):
    await self.open()
    return self

  @aexit_handler
  async def __aexit__(self):
    await self.close()

  async def open(self):
    if self._closing:
      raise AMFDeviceConnectionError

    self._read_task = asyncio.create_task(self._read_loop())

  async def close(self):
    """
    Closes the device.

    Raises
      AMFDeviceConnectionLostError: If the device was already closed.
    """

    if self._closing:
      raise AMFDeviceConnectionError

    self._closing = True

    # Cancel the read task, if any

    if self._read_task:
      self._read_task.cancel()

      try:
        await self._read_task
      except asyncio.CancelledError:
        pass

    # Wait for all futures to raise, as planned by the read task on exit

    futures = set(self._query_futures) | ({self._busy_future} if self._busy_future else set())

    if futures:
      await asyncio.wait(futures)

    assert self._serial

    self._serial.close()
    self._serial = None

  @staticmethod
  def list():
    """
    Lists visible devices.

    Yields
      Instances of `AMFDeviceInfo`.
    """

    for item in serial.tools.list_ports.comports():
      yield AMFDeviceInfo(address=item.device)


__all__ = [
  'AMFDevice',
  'AMFDeviceConnectionError',
  'AMFDeviceInfo'
]
